#
# DriveNets DNOS quirks
#
import uuid

from box import Box

from . import _Quirks

#
# cDNOS reads two per-instance environment variables, and neither can be a static part of the
# device definition:
#
#  * CDNOS_NAME becomes the DNOS hostname. Without it the node inherits the containerlab long
#    name (clab-<lab>-<node>), so "show isis neighbors" reports peers under a name that is not
#    the netlab node name. The entrypoint writes /tmp/exthostname from it, which is why the
#    vendor guide's "exec: echo ... > /tmp/exthostname" is redundant.
#  * CDNOS_UUID derives the mgmt0 MAC address and MUST be unique per instance. It defaults to a
#    random UUID, which is unique but changes on every "netlab up" -- and a management MAC that
#    moves invalidates the DHCP lease and the ARP entry of anything that had been talking to the
#    node. Deriving it from the lab and node name instead keeps it both unique and stable.
#
# The multilab id is part of the UUID input because that is what separates two concurrent
# instances of the SAME topology: without it they would agree on a management MAC.
#
CDNOS_UUID_NS = uuid.uuid5(uuid.NAMESPACE_DNS, 'cdnos.netlab.tools')


def cdnos_container_identity(node: Box, topology: Box) -> None:
  ml_id = topology.get('defaults.multilab.id', 0)
  node.clab.env.CDNOS_NAME = node.name
  node.clab.env.CDNOS_UUID = str(uuid.uuid5(CDNOS_UUID_NS, f'{ml_id}/{topology.name}/{node.name}'))


class DNOS(_Quirks):

  @classmethod
  def device_quirks(self, node: Box, topology: Box) -> None:
    # Hardware nodes (external provider) are not ours to name and have no container environment.
    if node.get('provider', topology.provider) == 'clab':
      cdnos_container_identity(node, topology)
