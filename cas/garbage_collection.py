

def GarbageCollectCASEntries(subref_lookup_map=None):
  """Runs a garbage collection pass.

  Args:
    subref_lookup_map - A dict of {data_type: (f(data) -> list(subrefs))}
  """
  subref_lookup_map = subref_lookup_map or {}
