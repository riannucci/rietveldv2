from . import types

from framework import utils


TYPE_MAP = types.CASTypeRegistry()


TYPE_MAP('text/universal; charset=UTF-8')
def text_universal_utf8(data):
  """Like text/plain, but all lines are deliniated by \\n."""
  decoded = data.decode('utf-8')
  assert '\r' not in decoded
  return utils.completed_future(decoded)


TYPE_MAP('image/gif')
def image_gif(data):
  assert data.startswith(("GIF87a", "GIF89a"))


TYPE_MAP('image/png')
def image_png(data):
  assert data.startswith('\x89PNG\r\n\x1a\n')


TYPE_MAP('image/jpeg')
def image_jpeg(data):
  assert data.startswith('\xff\xd8')


TYPE_MAP('image/svg+xml')
def image_svg_xml(data):
  # TODO(iannucci): Use real SVG schema?
  root = TYPE_MAP['application/xml'](data)
  assert root.tag == 'svg'
  return utils.completed_future(root)


TYPE_MAP('application/json')
def application_json(data):
  import json
  return utils.completed_future(json.loads(data))


TYPE_MAP('application/octet-stream')
def application_octet_stream(_data):
  pass


TYPE_MAP('application/xml')
def application_xml(data):
  from lxml import etree as ElementTree
  return utils.completed_future(ElementTree.fromstring(data))
