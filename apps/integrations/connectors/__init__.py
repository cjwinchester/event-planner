"""Every connector module; importing this package registers them.

To add a connector: create a module/package here with a BaseConnector
subclass decorated with @register, then add it to the import list below.
"""

from . import local_storage, memberclicks, membersuite, s3, vimeo, zoom  # noqa: F401
