#!/usr/local/bin/python

import os, sys

def url_test():
  import urls
  return {'urls': map(str, urls.urlpatterns)}

def GenTests(test):
  expected = os.path.splitext(__file__)[0]
  yield test(url_test, expected)
