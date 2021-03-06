#!/usr/bin/env python
#
# Copyright 2008 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""A collaborative slide making web application built on Google App Engine.
   Based on tasks list app by Bret Taylor."""

import datetime
import os
import random
import string
import sys
import logging
import re
import json

import webapp2

from webapp2_extras.appengine.users import login_required
from webapp2_extras import jinja2
from google.appengine.api import users
from google.appengine.ext import db
from google.appengine.api import urlfetch

# Set to true if we want to have our webapp print stack traces, etc
_DEBUG = True

def is_devserver():
    return os.environ['SERVER_SOFTWARE'].startswith('Dev')


def get_version():
    if is_devserver():
        return 'devserver'
    else:
        return os.environ.get('CURRENT_VERSION_ID')


# Returns unix time stamp
def datetime_to_timestamp(datetime):
    import calendar
    if datetime:
        return calendar.timegm(datetime.utctimetuple())
    else:
        return None

def datetime_to_iso(date):
    if date:
        return date.isoformat()
    else:
        return None


class SlideSet(db.Model):
  """A SlideSet is that encompasses all the slides.

  Other than the slides referring to it, a SlideSet just has meta-data, like
  whether it is published and the date at which it was last updated.
  """
  title      = db.StringProperty(required=False)
  created    = db.DateTimeProperty(auto_now_add=True)
  updated    = db.DateTimeProperty(auto_now=True)
  published  = db.BooleanProperty(default=False)
  slide_ids  = db.ListProperty(int)
  creator     = db.UserProperty()

  def get_slides(self):
    slide_keys = []
    for id in self.slide_ids:
      slide_keys.append(db.Key.from_path('Slide', id))
    return db.get(slide_keys)

  def remove_slide(self, slide_id):
    try:
      self.slide_ids.remove(int(slide_id))
      self.save()
      return True
    except ValueError:
      logging.info('Slide id not in list')
      return False

  def to_dict(self, with_slides=False):
    self_dict = {'id':      self.key().id(),
                 'created': datetime_to_timestamp(self.created),
                 'updated': datetime_to_timestamp(self.updated),
                'title':    self.title,
                'published': self.published,
                'slideIds': self.slide_ids}
    if with_slides:
      slides_dict = []
      slides = self.get_slides()
      for slide in slides:
        if slide is None:
          continue
        slide_dict = slide.to_dict()
        slide_dict['setId'] = self.key().id()
        slides_dict.append(slide_dict)
      self_dict['slides'] = slides_dict
    return self_dict

  def to_json(self, with_slides=False):
    return json.dumps(self.to_dict(with_slides=with_slides))

  def current_user_has_access(self):
    return self.user_has_access(users.get_current_user())
  
  def user_has_access(self, user):
    return True
    #return (user.email == self.creator.email)

  @staticmethod
  def get_current_user_sets():
    """Returns the slidesets that the current user has access to."""
    return SlideSet.get_user_sets(users.get_current_user())

  @staticmethod
  def get_user_sets(user):
    """Returns the slidesets that the given user has access to."""
    if not user: 
      return []
    slide_sets = SlideSet.all().filter('creator =', user)
    return slide_sets


class Slide(db.Model):
  """Represents a single slide in a slide set.
  """

  content = db.TextProperty()
  index = db.IntegerProperty()
  created = db.DateTimeProperty(auto_now_add=True)
  updated = db.DateTimeProperty(auto_now=True)

  def to_dict(self):
    return {'id':        self.key().id(),
            'content':   self.content}

  def to_json(self):
    return json.dumps(self.to_dict())


class BaseRequestHandler(webapp2.RequestHandler):
  """Supplies a common template generation function.

  When you call generate(), we augment the template variables supplied with
  the current user in the 'user' variable and the current webapp request
  in the 'request' variable.
  """

  @webapp2.cached_property
  def jinja2(self):
        return jinja2.get_jinja2(app=self.app)

  def generate(self, template_name, template_values={}):
    self.response.write(self.get_html(template_name, template_values))
    
  def get_html(self, template_name, template_values={}):

    # Start off in debug on devserver, and not on prod
    is_debug = is_devserver()
    debug = self.request.get('debug')
    # But always allow override via debug param
    if debug:
      if debug == 'true':
        is_debug = True
      else:
        is_debug = False
    
    values = {
        'request': self.request,
        'user': users.get_current_user(),
        'login_url': users.create_login_url(self.request.uri),
        'logout_url': users.create_logout_url('http://%s/' % (
            self.request.host,)),
        'debug': is_debug,
        'version': get_version(),
        }
    values.update(template_values)
    return self.jinja2.render_template(template_name, **values)
    

class InboxPage(BaseRequestHandler):
  """Lists all the slide sets for the current user."""

  @login_required
  def get(self):
    self.generate('inbox.html', {})


class SlideSetViewPage(BaseRequestHandler):

  def get(self, slide_set_id):
    slide_set = SlideSet.get_by_id(int(slide_set_id))
    if not slide_set:
      self.error(404)
      return

    if not (slide_set.current_user_has_access() or slide_set.published):
      self.error(403)

    template_name = 'viewer.html'
    template_values = {'slide_set': slide_set}
      
    self.response.headers['Content-Type'] = 'text/html'
    self.generate(template_name, template_values)


class SlideSetEditPage(BaseRequestHandler):

  def get(self, slide_set_id):
    slide_set = SlideSet.get_by_id(int(slide_set_id))
    if not slide_set:
      self.error(403)
      return

    can_edit = False
    if slide_set.current_user_has_access():
      can_edit = True
    else:
      if slide_set.published:
        self.redirect('viewer/set/%s' % slide_set_id)
      else:
        if users.get_current_user():
          self.error(403)
        else:
          self.redirect(users.create_login_url(self.request.uri))
        return

    template_name = 'editor.html'
    template_values = {
        'can_edit': can_edit,
        'slide_set': slide_set
        }
      
    self.response.headers['Content-Type'] = 'text/html'
    self.generate(template_name, template_values)


class APIHandler(webapp2.RequestHandler):

  body_json = None

  def get_body(self):
    if self.body_json is None:
      self.body_json = json.loads(self.request.body)
    return self.body_json

  def get_from_body(self, key):
    body_json = self.get_body()
    return body_json.get(key)

  def get_slide(self, slide_id):
    return Slide.get_by_id(int(slide_id))

  def get_slide_set(self, slide_set_id):
    return SlideSet.get_by_id(int(slide_set_id))

  def write_error(self, error):
    return '{"status": "error"}'


class InboxAPI(APIHandler):

  def get(self):
    slide_sets = []
    for slide_set in  SlideSet.get_current_user_sets():
      slide_sets.append(slide_set.to_dict(with_slides=False))
    response = {'slidesets': slide_sets}
    self.response.out.write(json.dumps(response))


class SlideAPI(APIHandler):

  def post(self, slide_set_id):
    slide_set    = self.get_slide_set(slide_set_id)
    slide = Slide()
    slide.put()
    slide_set.slide_ids.append(slide.key().id())
    slide_set.put()
    self.response.write(slide.to_json())

  def get(self, slide_set_id, slide_id):
    slide = self.get_slide(slide_id)
    self.response.headers['Content-Type'] = 'application/json'
    self.response.write(slide.to_json())
  
  def put(self, slide_set_id, slide_id):
    body         = self.get_body()
    content      = body['content']
    slide = self.get_slide(slide_id)
    slide.content = content
    slide.put()
    self.response.write(slide.to_json())

  def delete(self, slide_set_id, slide_id):
    slide_set    = self.get_slide_set(slide_set_id)
    if not slide_set.remove_slide(slide_id):
      self.write_error()


class SlideSetAPI(APIHandler):

  def post(self):
    body         = self.get_body()
    title        = body['title']
    user         = users.get_current_user()
    if not user:
      self.error(403)
      return
    slide_set = SlideSet(title=title, creator=user)
    slide_set.put()
    self.response.write(slide_set.to_json(with_slides=True))

  def get(self, slide_set_id):
    slide_set    = SlideSet.get_by_id(int(slide_set_id))
    self.response.headers['Content-Type'] = 'application/json'
    self.response.write(slide_set.to_json(with_slides=True))
  
  def put(self, slide_set_id):
    slide_set    = SlideSet.get_by_id(int(slide_set_id))
    body         = json.loads(self.request.body)
    logging.info(body)
    title        = self.get_from_body('title')
    published    = self.get_from_body('published')
    slide_ids    = self.get_from_body('slide_ids')
    if title:
      slide_set.title     = title
    if published:
      slide_set.published = published
    if slide_ids:
      slide_set.slide_ids = slide_ids
    slide_set.put()
    self.response.write(slide_set.to_json(with_slides=True))

  def delete(self, slide_set_id):
    slide_set    = SlideSet.get_by_id(int(slide_set_id))
    slide_set.delete()

app = webapp2.WSGIApplication([
      ('/',                          InboxPage),
      (r'/edit/set/(.*)',             SlideSetEditPage),
      (r'/view/set/(.*)',             SlideSetViewPage),
      ('/api/inbox',                 InboxAPI),
      (r'/api/sets/(.*)/slides/(.*)', SlideAPI),
      (r'/api/sets/(.*)/slides',      SlideAPI),
      (r'/api/sets/(.*)',             SlideSetAPI),
      ('/api/sets',                  SlideSetAPI),
      ], debug=_DEBUG)


if __name__ == '__main__':
  main()
