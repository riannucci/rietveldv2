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

from . import types

from framework import utils


TYPE_MAP = types.CASTypeRegistry()


@TYPE_MAP(charset='utf-8')
def charset_utf8(data):
  return data.decode('utf-8')


@TYPE_MAP(charset='ascii')
def charset_ascii(data):
  return data.decode('ascii')


@TYPE_MAP('text/plain', require_charset=('ascii', 'utf-8'))
def text_plain(data):
  # TODO(iannucci): Support more than one line ending type?
  splitter = utils.LazyLineSplitter(data, '\n')
  assert all(len(l) <= 300 for l in splitter)
  return data


@TYPE_MAP('image/gif')
def image_gif(data):
  assert data.startswith(("GIF87a", "GIF89a"))


@TYPE_MAP('image/png')
def image_png(data):
  assert data.startswith('\x89PNG\r\n\x1a\n')


@TYPE_MAP('image/jpeg')
def image_jpeg(data):
  assert data.startswith('\xff\xd8')


@TYPE_MAP('image/svg+xml')
def image_svg_xml(data):
  # TODO(iannucci): Use real SVG schema?
  root = TYPE_MAP['application/xml'](data)
  assert root.tag == 'svg'
  return root


@TYPE_MAP('application/json', require_charset='utf-8')
def application_json(data):
  import json
  return json.loads(data)


@TYPE_MAP('application/octet-stream')
def application_octet_stream(_data):
  pass


@TYPE_MAP('application/xml', require_charset='utf-8')
def application_xml(data):
  from lxml import etree as ElementTree
  return ElementTree.fromstring(data)
