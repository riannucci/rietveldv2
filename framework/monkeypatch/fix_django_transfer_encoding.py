from django.http import multipartparser

if not hasattr(multipartparser.Parser, 'is_fixed'):
  cte_header = 'content-transfer-encoding'

  class FixedParser(multipartparser.Parser):
    is_fixed = True

    def __iter__(self):
      for item_type, meta_data, stream in super(FixedParser, self).__iter__():
        cte = meta_data.get(cte_header)
        if isinstance(cte, tuple):
          meta_data[cte_header] = cte[0].strip()
        yield item_type, meta_data, stream

  multipartparser.Parser = FixedParser
