import logging
logger = logging.getLogger(__name__)

import base64
import hashlib
import hmac
import itertools
import mimetools
import mimetypes

import urllib
import urllib2

import facebook

from django.conf import settings
from django.shortcuts import redirect
from django.utils import simplejson
from django.utils.http import urlquote

_parse_json = lambda s: simplejson.loads(s)

def base64_url_decode(s):
    return base64.urlsafe_b64decode(s.encode("utf-8") + '=' * (4 - len(s) % 4))

def parseSignedRequest(signed_request, secret=None):
    """
    adapted from from http://web-phpproxy.appspot.com/687474703A2F2F7061737469652E6F72672F31303536363332
    """
    
    if not secret: secret = settings.FACEBOOK_APP_SECRET
    
    (encoded_sig, payload) = signed_request.split(".", 2)

    sig = base64_url_decode(encoded_sig)
    data = simplejson.loads(base64_url_decode(payload))

    if data.get("algorithm").upper() != "HMAC-SHA256":
        return {}

#    """ i dont know why, but this crashes in one of my project. but i dont need it anyway """
#    expected_sig = hmac.new(secret, msg=payload, digestmod=hashlib.sha256).digest()
#    if sig != expected_sig:
#        return {}

    return data

def get_REST(method, params):
    query = 'https://api.facebook.com/method/%s?format=json&%s' % (method, urllib.urlencode(params))
    file = urllib.urlopen(query)
    raw = file.read()
    
    logger.debug('facebook REST response raw: %s, query: %s' % (raw, query))
    
    try:
        response = _parse_json(raw)
    except:
        response = {'response' : raw }
    finally:
        file.close()
    
    return response

def get_FQL(fql, access_token=None):
    query = 'https://api.facebook.com/method/fql.query?format=json'
    
    params = {'query' : fql}
    
    if access_token:
        params.update({'access_token' : access_token})
    
    file = urllib.urlopen(query, urllib.urlencode(params))
    raw = file.read()
    
    logger.debug('facebook FQL response raw: %s, query: %s, FQL: %s' % (raw, query, fql))
    
    try:
        response = _parse_json(raw)
    finally:
        file.close()
    
    return response

class Graph(facebook.GraphAPI):
    """ The Base Class for a Facebook Graph. Inherits from the Facebook SDK Class. """
    """ Tries to get a facebook graph using different methods.
    * via access_token: that one is simple
    * via request cookie (access token)
    * via application -> create an accesstoken for an application if requested.
    Needs OAuth2ForCanvasMiddleware to deal with the signed Request and Authentication code.
    """
    def __init__(self, request, access_token=None, app_secret=settings.FACEBOOK_APP_SECRET,
                 app_id=settings.FACEBOOK_APP_ID, code=None, request_token=True):
        super(Graph, self).__init__(access_token)
        self.HttpRequest = request
        self.get_fb_sesison(request)
        self._me, self._user = None, None
        self.app_id, self.app_secret = app_id, app_secret
        self.via = 'No token requested'
        self.app_is_authenticated = self.fb_session['app_is_authenticated'] \
                                    if self.fb_session.get('app_is_authenticated', False) else True #Assuming True.                              
        if access_token:
            self.via = 'access_token'
        elif self.fb_session.get('access_token', None):
            self.get_token_from_session()
        
        #Clientseitige Authentication schreibt das Token ueber die Middleware ins Cookie.    
        elif request.COOKIES.get('fbs_%i' % app_id, None): 
            self.get_token_from_cookie()
            
        elif request_token: #get the app graph
            self.get_token_from_app()
    
    def get_token_from_session(self):
        self.access_token = self.fb_session.get('access_token')
        self._user = self.fb_session.get('user_id', None)
        if not self._user: 
            self.fb_session['app_is_authenticated'] = False
            self.HttpRequest.session.modified = True
        self.via = 'session'
    
    def get_token_from_cookie(self):
        cookie = self.get_user_from_cookie(self.HttpRequest.COOKIES, self.app_id, self.app_secret)
        self.access_token = cookie['access_token']
        self._user = cookie['uid']
        self.fb_session.update({'access_token': self.access_token, 'user_id': self._user })
        self.HttpRequest.session.modified = True
        self.via = 'cookie'
        
    def get_token_from_app(self):
        access_dict = {'type' : 'client_cred', 'client_secret' : self.client_secret, 'client_id' : self.client_id}
        file = urllib.urlopen('https://graph.facebook.com/oauth/access_token?%s' 
                              % urllib.urlencode(access_dict))
        raw = file.read()
        try:
            response = _parse_json(raw)
            if response.get("error"):
                raise facebook.GraphAPIError(response["error"]["type"],
                                             response["error"]["message"])
            else:
                raise facebook.GraphAPIError('GET_GRAPH', 'Facebook returned json (%s), expected access_token' % response)
        except:
            # if the response ist not json, it is the access token. Write it back to the session.
            if raw.find('=') > -1:
                self.fb_session['access_token'] = access_token
                self.HttpRequest.session.modified = True
            else:
                raise facebook.GraphAPIError('GET_GRAPH', 'Facebook returned bullshit (%s), expected access_token' % response)
        finally:
            file.close()
        self.via = 'application'
        
        
    def get_fb_session(self, request):
        fb = request.session.get('facebook', None)
        if not fb:
            request.session.update({'facebook': {'app_is_authenticated': True }})
            fb = request.session['facebook']
        self.fb_session = fb   
         
    def _get_me(self):
        if not self.access_token or not self.fb_session['app_is_authenticated']:
            return None
        else:
            try:
                self._me = self.request('me')
                self._user = self._me['id']
                self.fb_session.update({'user_id': self._user, 'access_token': self.access_token })
                self.HttpRequest.session.modified = True
            except facebook.GraphAPIError as e:
                logger.debug('could not use the accesstoken via %s: %s' %(self.via, e.message))
                self.fb_session['app_is_authenticated'] = False
            return self._me
            
    @property
    def me(self): #Is now a lazy property.
        if self._me:
            return self._me
        else:
            self._get_me()
    
    @property        
    def user(self):
        if self._user:
            return self._user
        else:
            return self.me.get('id', None)
            
            

def get_graph(request=None, *args, **kwargs):
    if not request: 
        raise facebook.GraphAPIError('GET_GRAPH', 'get_graph requires the request as its first argument.')
    return Graph(request, *args, **kwargs)
    

def post_image(access_token, image, message, object='me'):
    form = MultiPartForm()
    form.add_field('access_token', access_token)
    form.add_field('message', message)
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
              str(value),
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
              str(body)
            ]
            for field_name, filename, content_type, body in self.files
            )
        
        # Flatten the list and add closing boundary marker,
        # then return CR+LF separated data
        flattened = list(itertools.chain(*parts))
        flattened.append('--' + self.boundary + '--')
        flattened.append('')
        return '\r\n'.join(flattened)


def redirect_GET_session(to, request, permanent=False):
    response = redirect(to, permanent)
    cookie_name = settings.SESSION_COOKIE_NAME
    
    if request.COOKIES.has_key(cookie_name):
        location = response._headers['location'][1]
        separator = '&' if '?' in location else '?'
        response._headers['location'] = ('Location' , '%s%s%s=%s' % (location, 
                        separator, cookie_name, 
                        request.COOKIES.get(cookie_name, '')))
        return response
    else:
        return response
