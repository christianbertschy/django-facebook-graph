import logging
logger = logging.getLogger(__name__)

import itertools
import mimetools
import mimetypes

import urllib
import urllib2

import facebook

from django.conf import settings
from django.utils import simplejson
from django.utils.http import urlquote

_parse_json = lambda s: simplejson.loads(s)

def get_FQL(fql):
    query = 'https://api.facebook.com/method/fql.query?format=json&query=%s' % urlquote(fql)
    file = urllib.urlopen(query)
    raw = file.read()
    
    logger.debug('facebook FQL response raw: %s, query: %s, FQL: %s' % (raw, query, fql))
    
    try:
        response = _parse_json(raw)
    finally:
        file.close()
    
    return response

def get_graph(request=None, access_token=None, client_secret=None, client_id=None):
    """ Tries to get a facebook graph by different methods.
    
    * via access_token: that one is simple
    * via request cookie (access token)
    * via application -> make an accesstoken for an application
    
    """
    
    # if no application is specified, get default from settings
    if not client_secret: client_secret = settings.FACEBOOK_APP_SECRET
    if not client_id: client_id = settings.FACEBOOK_APP_ID
    
    if access_token:
            graph = facebook.GraphAPI(access_token)
            graph.via = 'access_token'
            logger.debug('got graph via access_token: %s' % graph.access_token)
            return graph
    
    if request:
        cookie = facebook.get_user_from_cookie(request.COOKIES, client_id, client_secret)
        
        if cookie != None:
            graph = facebook.GraphAPI(cookie["access_token"])
            graph.user = cookie['uid']
            graph.via = 'cookie'
            logger.debug('got graph via cookie. access_token: %s' % graph.access_token) 
            return graph
        else:
            logger.debug('could not get graph via cookie. cookies: %s' % request.COOKIES)
    
    # get token by application
    file = urllib.urlopen('https://graph.facebook.com/oauth/access_token?%s' 
                          % urllib.urlencode({'type' : 'client_cred',
                                              'client_secret' : client_secret,
                                              'client_id' : client_id}))
    raw = file.read()
    
    try:
        response = _parse_json(raw)
        if response.get("error"):
            raise facebook.GraphAPIError(response["error"]["type"],
                                         response["error"]["message"])
        else:
            raise facebook.GraphAPIError('GET_GRAPH', 'Facebook returned json (%s), expected access_token' % response)
    except:
        # if the response ist not json, it is
        if raw.find('=') > -1:
            access_token = raw.split('=')[1]
        else:
            raise facebook.GraphAPIError('GET_GRAPH', 'Facebook returned bullshit (%s), expected access_token' % response)
    finally:
        file.close()
    
    graph = facebook.GraphAPI(access_token)
    graph.via = 'application'
    logger.debug('got graph via application: %s. access_token: %s' %(client_id, graph.access_token)) 
    return graph
    

def post_image(access_token, image, message, object='me'):
    form = MultiPartForm()
    form.add_field('access_token', access_token)
    form.add_field('message', '')
    form.add_file('image', 'image.jpg', image)
    
    request = urllib2.Request('https://graph.facebook.com/%s/photos' % object)
    logger.debug('posting photo to: https://graph.facebook.com/%s/photos %s' % (object, image))
    #request.add_header('User-agent', 'Chef de cuisine - FB App')
    body = str(form)
    request.add_header('Content-type', form.get_content_type())
    request.add_header('Content-length', len(body))
    request.add_data(body)
    
    raw = urllib2.urlopen(request).read()
    logger.debug('facebook response raw: %s' % raw)
    
    try:
        response = _parse_json(raw)
    except:
        raise facebook.GraphAPIError('GET_GRAPH', 'Facebook returned bullshit (%s), expected json' % response)
            
    """ in some cases, response is not an object """
    if response:
        if response.get("error"):
            raise GraphAPIError(response["error"]["type"],
                                response["error"]["message"])
    return response

    
# from http://www.doughellmann.com/PyMOTW/urllib2/
class MultiPartForm(object):
    """Accumulate the data to be used when posting a form."""

    def __init__(self):
        self.form_fields = []
        self.files = []
        self.boundary = mimetools.choose_boundary()
        return
    
    def get_content_type(self):
        return 'multipart/form-data; boundary=%s' % self.boundary

    def add_field(self, name, value):
        """Add a simple field to the form data."""
        self.form_fields.append((name, value))
        return

    def add_file(self, fieldname, filename, fileHandle, mimetype=None):
        """Add a file to be uploaded."""
        body = fileHandle.read()
        if mimetype is None:
            mimetype = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        self.files.append((fieldname, filename, mimetype, body))
        return
    
    def __str__(self):
        """Return a string representing the form data, including attached files."""
        # Build a list of lists, each containing "lines" of the
        # request.  Each part is separated by a boundary string.
        # Once the list is built, return a string where each
        # line is separated by '\r\n'.  
        parts = []
        part_boundary = '--' + self.boundary
        
        # Add the form fields
        parts.extend(
            [ part_boundary,
              'Content-Disposition: form-data; name="%s"' % name,
              '',
              value,
            ]
            for name, value in self.form_fields
            )
        
        # Add the files to upload
        parts.extend(
            [ part_boundary,
              'Content-Disposition: file; name="%s"; filename="%s"' % \
                 (field_name, filename),
              'Content-Type: %s' % content_type,
              '',
              body,
            ]
            for field_name, filename, content_type, body in self.files
            )
        
        # Flatten the list and add closing boundary marker,
        # then return CR+LF separated data
        flattened = list(itertools.chain(*parts))
        flattened.append('--' + self.boundary + '--')
        flattened.append('')
        return '\r\n'.join(flattened)