# Copyright 2013 Google Inc.
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

# Re-puts entities of a given type, to set newly added properties.
#
# To run this script:
#  - Make sure App Engine library (incl. yaml) is in PYTHONPATH.
#  - Make sure that the remote API is included in app.yaml.
#  - Run "tools/appengine_console.py APP_ID".
#  - Import this module.
#  - Import models from codereview.
#  - update_entities.run(models.Issue) updates issues.


import logging
from google.appengine.ext import db
from codereview import models
import urllib2

def run(model_class, batch_size=100, last_key=None):
    while True:
      q = model_class.all()
      if last_key:
        q.filter('__key__ >', last_key)
      q.order('__key__')
      this_batch_size = batch_size

      while True:
        try:
          try:
            batch = q.fetch(this_batch_size)
          except urllib2.URLError, err:
            if 'timed out' in str(err):
              raise db.Timeout
            else:
              raise
          break
        except db.Timeout:
          logging.warn("Query timed out, retrying")
          if this_batch_size == 1:
            logging.critical("Unable to update entities, aborting")
            return
          this_batch_size //= 2

      if not batch:
        break

      keys = None
      while not keys:
        try:
          keys = db.put(batch)
        except db.Timeout:
          logging.warn("Put timed out, retrying")

      last_key = keys[-1]
      print "Updated %d records" % (len(keys),)
