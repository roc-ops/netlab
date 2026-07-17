#
# Arrcus ArcOS quirks (issue #39)
#
from box import Box

from . import _Quirks, need_ansible_collection


class ArcOS(_Quirks):

  @classmethod
  def device_quirks(cls, node: Box, topology: Box) -> None:
    pass

  def check_config_sw(self, node: Box, topology: Box) -> None:
    need_ansible_collection(node,'arrcus.arcos',version='2.0.0')
