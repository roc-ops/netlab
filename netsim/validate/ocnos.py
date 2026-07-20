# Top-level OcNOS validation plugin
#
# OcNOS show output is CLI text (not JSON) fetched over the Ansible validation
# transport (netsim/cli/connect.py ansible_connect); the per-module validators use
# the exec_/valid_ (screen-scrape) contract. See netsim/validate/<module>/ocnos.py.
from netsim.validate.bgp.ocnos import *
from netsim.validate.isis.ocnos import *
from netsim.validate.ospf.ocnos import *
