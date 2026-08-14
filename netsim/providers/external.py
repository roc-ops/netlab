#
# External provider module
#
from box import Box

from ..utils import log, status
from . import _Provider


class External(_Provider):

  def pre_transform(self,topology : Box) -> None:
    _Provider.pre_transform(self,topology)
    if not topology.get('defaults.multilab.id',None):
      topology.defaults.multilab.id = f'external_{topology.name}'

  def augment_node_data(self, node: Box, topology: Box) -> None:
    log.print_verbose('Augmenting node data for External')
    # Cleanup MGMT MAC Address (since it's useless for us)
    node.mgmt.pop('mac',None)

    # A device reached through the external provider ALREADY EXISTS -- netlab did not create it
    # and does not own it outright. It has a hostname, and on a router in service the loopback is
    # usually load-bearing: the BGP router-id, and the route-distinguisher endpoint for L2VPN
    # services. Rendering node identity as if netlab owned it makes the deploy REPLACE those.
    #
    # Device templates that can be pointed at physical hardware must therefore guard hostname and
    # loopback rendering on 'netlab_manage_identity', which defaults to False here and can be set
    # to True per node when netlab really is the owner of record.
    if 'netlab_manage_identity' not in node:
      node.netlab_manage_identity = False

  def pre_start_lab(self, topology: Box) -> None:
    log.print_verbose('pre-start hook for External')
    if log.QUIET:
      return
    
    print('*** Please make sure your physical topology reflects the following cabling:')
    print('')
    with open('external.txt', 'r') as f:
      print(f.read())
    print('')
    print('*** Please make sure your physical topology reflects the above cabling.',flush=True)
    if input('Do you want to continue [y/n]: ').lower() != 'y':
      status.unlock_directory()
      status.remove_lab_status(topology)
      log.fatal('Aborting...')
