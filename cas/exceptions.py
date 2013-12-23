
class CASError(Exception):
  pass


class CASValidationError(CASError):
  pass


class CASUnknownDataType(CASError):
  def __init__(self, type_map, data_type):
    self.type_map = type_map
    self.data_type = data_type
    super(CASUnknownDataType, self).__init__(
      "Unknown data_type: %s" % data_type)
