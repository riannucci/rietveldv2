import functools
import logging

from . import common


class CASValidationError(common.CASError):
  pass


class CASTypeRegistry(dict):
  """A dict with a __call__() decorator method for making |type_map|s for
  |CASEntry.new()|.

  Pro Tip: You can initialize a new CASTypeRegistry from an existing one.
    >>> import default_data_types
    >>> my_reg = CASTypeRegistry(default_data_types.TYPE_MAP)
    >>> my_reg
    {
      'text/universal; charset=UTF-8': <function ...>,
      ...
    }
  """

  def __call__(self, *type_names):
    """Use to decorate a validator function for CAS |data_type|s.

    >>> reg = CASTypeRegistry()
    >>> @reg("application/cool_type")
    ... def application_cool_type(data):  # Function name doesn't matter
    ...   return MyCoolType(data)  # Throws if data is unparsable
    ...
    >>> reg['application/cool_type]('\xc0\x01compatible_data')
    [out] <__main__.MyCoolType object at 0x101b7dfd0>
    >>> reg['application/cool_type]('bad data!')
    [log] ERROR:root:While validating 'application/cool_type'
    [log] Traceback (most recent call last):
    [log]   ... real traceback with implementation details of MyCoolType
    [log] MyCoolTypeException: Data didn't match secret key "\xc0\x01"!
    [out] Traceback (most recent call last):
    [out]   ... Short traceback from CASTypeRegistry ...
    [out] models.CASValidationError: Invalid 'application/cool_type'
    """
    def inner(f):
      """Wraps f(data) to validate |type_names| MIME types.

      f is expected to raise an exception (the type doesn't matter), if data
      isn't compatible with the indicated |type_names|.

      f may optionally return a 'parsed' form of data which is more natural to
      work with in the python context. If f returns None, the wrapper function
      will return |data| instead, as a convenience.
      """
      @functools.wraps(f)
      def wrapped(data):
        try:
          ret = f(data)
          if ret is None:
            ret = data
          return ret
        except:
          # Log exception for debugging, but neuter the raised exception to
          # avoid leakage of implementation details.
          logging.exception("While validating '%s'" % type_names[0])
          raise CASValidationError("Invalid '%s'" % type_names[0])

      for name in type_names:
        self[name] = wrapped

      return wrapped
    return inner


