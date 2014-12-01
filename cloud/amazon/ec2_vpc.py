#!/usr/bin/python
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

DOCUMENTATION = '''
---
module: ec2_vpc
short_description: configure AWS virtual private clouds
description:
    - Create or terminates AWS virtual private clouds.  This module has a'''
''' dependency on python-boto.
version_added: "1.4"
options:
  cidr_block:
    description:
      - "The cidr block representing the VPC, e.g. 10.0.0.0/16"
    required: false, unless state=present
  instance_tenancy:
    description:
      - "The supported tenancy options for instances launched into the VPC."
    required: false
    default: "default"
    choices: [ "default", "dedicated" ]
  dns_support:
    description:
      - toggles the "Enable DNS resolution" flag
    required: false
    default: "yes"
    choices: [ "yes", "no" ]
  dns_hostnames:
    description:
      - toggles the "Enable DNS hostname support for instances" flag
    required: false
    default: "yes"
    choices: [ "yes", "no" ]
  subnet_ids:
    description:
      - 'A list of subnet IDs to keep on the VPC. If this argument is'''
''' supplied, only those subnets listed will be kept; others will be removed.'
    required: false
    default: null
    aliases: []
  vpc_id:
    description:
      - A VPC id to terminate when state=absent
    required: false
    default: null
    aliases: []
  resource_tags:
    description:
      - 'A dictionary array of resource tags of the form: { tag1: value1,'''
''' tag2: value2 }. Tags in this list are used in conjunction with CIDR'''
''' block to uniquely identify a VPC in lieu of vpc_id. Therefore, if'''
''' CIDR/Tag combination does not exits, a new VPC will be created.  VPC'''
''' tags not on this list will be ignored. Prior to 1.7, specifying a'''
''' resource tag was optional.'
    required: true
    default: null
    aliases: []
    version_added: "1.6"
  route_table_ids:
    description:
      - 'A list of route table IDs to keep on the VPC. If this argument is'''
''' supplied, only those tables listed will be kept; others will be removed.'
    required: false
    default: null
    aliases: []
  wait:
    description:
      - wait for the VPC to be in state 'available' before returning
    required: false
    default: "no"
    choices: [ "yes", "no" ]
    aliases: []
  wait_timeout:
    description:
      - how long before wait gives up, in seconds
    default: 300
    aliases: []
  state:
    description:
      - Create or terminate the VPC
    required: true
    default: present
    aliases: []
  region:
    description:
      - region in which the resource exists.
    required: false
    default: null
    aliases: ['aws_region', 'ec2_region']
  aws_secret_key:
    description:
      - AWS secret key. If not set then the value of the AWS_SECRET_KEY'''
''' environment variable is used.
    required: false
    default: None
    aliases: ['ec2_secret_key', 'secret_key' ]
  aws_access_key:
    description:
      - AWS access key. If not set then the value of the AWS_ACCESS_KEY'''
''' environment variable is used.
    required: false
    default: None
    aliases: ['ec2_access_key', 'access_key' ]
  validate_certs:
    description:
      - When set to "no", SSL certificates will not be validated for boto'''
''' versions >= 2.6.0.
    required: false
    default: "yes"
    choices: ["yes", "no"]
    aliases: []
    version_added: "1.5"

requirements: [ "boto" ]
author: Carson Gee
'''

EXAMPLES = '''
# Note: None of these examples set aws_access_key, aws_secret_key, or region.
# It is assumed that their matching environment variables are set.

# Basic creation example:
      ec2_vpc:
        state: present
        cidr_block: 172.23.0.0/16
        resource_tags: { "Environment":"Development" }
        region: us-west-2
      register vpc

# The absence or presence of subnets and route tables deletes or creates them
# respectively.
      local_action:
        module: ec2_vpc
        vpc_id: {{vpc.vpc_id}}
        subnet_ids:
          - {{private_subnet.subnet_id}}
          - {{public_subnet.subnet_id}}
        route_table_ids:
          - {{public_route_table.route_table_id}}
          - {{nat_route_table.route_table_id}}

# Removal of a VPC by id
      ec2_vpc:
        state: absent
        vpc_id: vpc-aaaaaaa
        region: us-west-2
If you have added elements not managed by this module, e.g. instances, NATs,
etc. then the delete will fail until those dependencies are removed.
'''


import sys  # noqa
import time

try:
    import boto.ec2
    import boto.vpc
    from boto.exception import EC2ResponseError
    HAS_BOTO = True
except ImportError:
    HAS_BOTO = False
    if __name__ != '__main__':
        raise


def vpc_json(vpc):
    """
    Retrieves vpc information from an instance
    ID and returns it as a dictionary
    """
    return({
        'id': vpc.id,
        'cidr_block': vpc.cidr_block,
        'dhcp_options_id': vpc.dhcp_options_id,
        'region': vpc.region.name,
        'state': vpc.state,
    })


def subnet_json(vpc_conn, subnet):
    return {
        'resource_tags': {t.name: t.value
                          for t in vpc_conn.get_all_tags(
                              filters={'resource-id': subnet.id})},
        'cidr': subnet.cidr_block,
        'az': subnet.availability_zone,
        'id': subnet.id,
    }


class AnsibleVPCException(Exception):
    pass


def find_vpc(vpc_conn, resource_tags=None, vpc_id=None, cidr=None):
    """
    Finds a VPC that matches a specific id or cidr + tags

    module : AnsibleModule object
    vpc_conn: authenticated VPCConnection connection object

    Returns:
    A VPC object that matches either an ID or CIDR and one or more tag values
    """
    if vpc_id is None and (cidr is None or not resource_tags):
        raise AnsibleVPCException(
            'You must specify either a vpc_id or a cidr block + list of'
            ' unique tags, aborting')

    found_vpcs = []

    # Check for existing VPC by cidr_block or id
    if vpc_id is not None:
        found_vpcs = vpc_conn.get_all_vpcs(None, {'vpc-id': vpc_id,
                                                  'state': 'available'})
    else:
        previous_vpcs = vpc_conn.get_all_vpcs(None, {'cidr': cidr,
                                                     'state': 'available'})

        for vpc in previous_vpcs:
            # Get all tags for each of the found VPCs
            vpc_tags = {t.name: t.value
                        for t in vpc_conn.get_all_tags(
                            filters={'resource-id': vpc.id})}

            # If the supplied list of ID Tags match a subset of the VPC Tags,
            # we found our VPC
            if all((k in vpc_tags and vpc_tags[k] == v
                    for k, v in resource_tags.items())):
                found_vpcs.append(vpc)

    if not found_vpcs:
        return None
    elif len(found_vpcs) == 1:
        return found_vpcs[0]

    raise AnsibleVPCException(
        'Found more than one vpc based on the supplied criteria, aborting')


def route_table_is_main(route_table):
    if route_table.id is None:
        return True
    for a in route_table.associations:
        if a.main:
            return True
    return False


def ensure_vpc_present(vpc_conn, vpc_id, cidr_block, instance_tenancy,
                       dns_support, dns_hostnames, subnet_ids,
                       route_table_ids, resource_tags, wait, wait_timeout,
                       check_mode):
    """
    Creates a new or modifies an existing VPC.

    module : AnsibleModule object
    vpc_conn: authenticated VPCConnection connection object

    Returns:
        A dictionary with information
        about the VPC and subnets that were launched
    """
    changed = False

    # Check for existing VPC by cidr_block + tags or id
    vpc = find_vpc(vpc_conn, resource_tags, vpc_id, cidr_block)

    if vpc is None:
        if check_mode:
            return {'changed': True}

        changed = True
        try:
            vpc = vpc_conn.create_vpc(cidr_block, instance_tenancy)
            vpc_id = vpc.id

            # wait here until the vpc is available
            pending = True
            wait_timeout = time.time() + wait_timeout
            while wait and wait_timeout > time.time() and pending:
                try:
                    pvpc = vpc_conn.get_all_vpcs(vpc.id)
                    if hasattr(pvpc, 'state'):
                        if pvpc.state == 'available':
                            pending = False
                    elif hasattr(pvpc[0], 'state'):
                        if pvpc[0].state == "available":
                            pending = False
                # sometimes vpc_conn.create_vpc() will return a vpc that can't
                # be found yet by vpc_conn.get_all_vpcs() when that happens,
                # just wait a bit longer and try again
                except boto.exception.BotoServerError as e:
                    if e.error_code != 'InvalidVpcID.NotFound':
                        raise
                if pending:
                    time.sleep(5)
            if wait and wait_timeout <= time.time():
                raise AnsibleVPCException(
                    'Wait for vpc availability timeout on {}'
                    .format(time.asctime())
                )
        except boto.exception.BotoServerError, e:
            raise AnsibleVPCException(
                '{}: {}'.format(e.error_code, e.error_message))

    # Done with base VPC, now change to attributes and features.

    # Add resource tags
    vpc_tags = {t.name: t.value
                for t in vpc_conn.get_all_tags(
                    filters={'resource-id': vpc.id})}

    if (resource_tags and
            not set(resource_tags.items()).issubset(set(vpc_tags.items()))):
        new_tags = {}

        for key, value in set(resource_tags.items()):
            if (key, value) not in set(vpc_tags.items()):
                new_tags[key] = value

        if new_tags:
            if check_mode:
                return {
                    'changed': True,
                    'vpc_id': vpc.id,
                    'vpc': vpc_json(vpc),
                    'subnets': []
                }

            vpc_conn.create_tags(vpc.id, new_tags)
            changed = True

    # boto doesn't appear to have a way to determine the existing
    # value of the dns attributes, so we just set them.
    # It also must be done one at a time.
    if not check_mode:
        vpc_conn.modify_vpc_attribute(vpc.id,
                                      enable_dns_support=dns_support)
        vpc_conn.modify_vpc_attribute(vpc.id,
                                      enable_dns_hostnames=dns_hostnames)

    # Process all subnet properties
    if subnet_ids is not None:
        current_subnets = vpc_conn.get_all_subnets(filters={'vpc_id': vpc.id})

        for subnet_id in subnet_ids:
            if not any((s.id == subnet_id for s in current_subnets)):
                raise AnsibleVPCException(
                    'Unknown subnet {0}'.format(subnet_id, e))

        for subnet in current_subnets:
            if subnet.id in subnet_ids:
                continue

            if check_mode:
                return {
                    'changed': True,
                    'vpc_id': vpc.id,
                    'vpc': vpc_json(vpc),
                    'subnets': [
                        subnet_json(vpc_conn, s)
                        for s in current_subnets
                        if s.id in subnet_ids
                    ]
                }

            try:
                vpc_conn.delete_subnet(subnet.id)
                changed = True
            except EC2ResponseError as e:
                raise AnsibleVPCException(
                    'Unable to delete subnet {0}, error: {1}'
                    .format(subnet.cidr_block, e))

    json_subnets = [subnet_json(vpc_conn, s)
                    for s in vpc_conn.get_all_subnets(
                        filters={'vpc_id': vpc.id})]

    if route_table_ids is not None:
        # old ones except the 'main' route table as boto can't set the main
        # table yet.
        current_route_tables = vpc_conn.get_all_route_tables(
            filters={'vpc-id': vpc.id})

        for route_table in current_route_tables:
            if (route_table.id in route_table_ids
                    or route_table_is_main(route_table)):
                continue

            if check_mode:
                return {
                    'changed': True,
                    'vpc_id': vpc.id,
                    'vpc': vpc_json(vpc),
                    'subnets': json_subnets,
                }

            try:
                vpc_conn.delete_route_table(route_table.id)
                changed = True
            except EC2ResponseError, e:
                raise AnsibleVPCException(
                    'Unable to delete old route table {0}, error: {1}'
                    .format(route_table.id, e))

    return {
        'changed': changed,
        'vpc_id': vpc.id,
        'vpc': vpc_json(vpc),
        'subnets': json_subnets,
    }


def ensure_vpc_absent(vpc_conn, resource_tags, vpc_id, cidr, check_mode):
    """
    Terminates a VPC

    module: Ansible module object
    vpc_conn: authenticated VPCConnection connection object
    vpc_id: a vpc id to terminate
    cidr: The cidr block of the VPC - can be used in lieu of an ID

    Returns a dictionary of VPC information
    about the VPC terminated.

    If the VPC to be terminated is available
    "changed" will be set to True.

    """

    vpc = find_vpc(vpc_conn, resource_tags, vpc_id, cidr)

    changed = False
    if check_mode:
        if vpc is None:
            return {'changed': False, 'vpc_id': vpc_id, 'vpc': {}}
        elif vpc.state == 'available':
            changed = True
        else:
            changed = False
    elif vpc is None or vpc.state == 'available':
        changed = False
    else:
        changed = True
        try:
            subnets = vpc_conn.get_all_subnets(filters={'vpc_id': vpc.id})
            for sn in subnets:
                vpc_conn.delete_subnet(sn.id)

            igws = vpc_conn.get_all_internet_gateways(
                filters={'attachment.vpc-id': vpc.id}
            )
            for igw in igws:
                vpc_conn.detach_internet_gateway(igw.id, vpc.id)
                vpc_conn.delete_internet_gateway(igw.id)

            rts = vpc_conn.get_all_route_tables(filters={'vpc_id': vpc.id})
            for rt in rts:
                rta = rt.associations
                is_main = False
                for a in rta:
                    if a.main:
                        is_main = True
                if not is_main:
                    vpc_conn.delete_route_table(rt.id)

            vpc_conn.delete_vpc(vpc.id)
        except EC2ResponseError, e:
            raise AnsibleVPCException(
                'Unable to delete VPC {0}, error: {1}'.format(vpc.id, e)
            )

    return {'changed': changed, 'vpc_id': vpc.id, 'vpc': vpc_json(vpc)}


def main():
    argument_spec = ec2_argument_spec()
    argument_spec.update(dict(
        vpc_id=dict(required=False),
        cidr_block=dict(required=False),
        resource_tags=dict(type='dict', required=False),
        instance_tenancy=dict(choices=['default', 'dedicated'],
                              default='default'),
        wait=dict(type='bool', default=False),
        wait_timeout=dict(type='int', default=300),
        dns_support=dict(type='bool', default=True),
        dns_hostnames=dict(type='bool', default=True),
        subnet_ids=dict(type='list', required=False),
        route_table_ids=dict(type='list', required=False),
        state=dict(choices=['present', 'absent'], default='present'),
    ))

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )
    if not HAS_BOTO:
        module.fail_json(msg='boto is required for this module')

    vpc_id = module.params.get('vpc_id')
    cidr_block = module.params.get('cidr_block')
    instance_tenancy = module.params.get('instance_tenancy')
    dns_support = module.params.get('dns_support')
    dns_hostnames = module.params.get('dns_hostnames')
    subnet_ids = module.params.get('subnet_ids')
    route_table_ids = module.params.get('route_table_ids')
    resource_tags = module.params.get('resource_tags')
    wait = module.params.get('wait')
    wait_timeout = module.params.get('wait_timeout')
    state = module.params.get('state')

    ec2_url, aws_access_key, aws_secret_key, region = get_ec2_creds(module)
    if not region:
        module.fail_json(msg="region must be specified")

    try:
        vpc_conn = boto.vpc.connect_to_region(
            region,
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key
        )
    except boto.exception.NoAuthHandlerFound, e:
        module.fail_json(msg=str(e))

    try:
        if module.params.get('state') == 'absent':
            result = ensure_vpc_absent(
                vpc_conn, resource_tags, vpc_id, cidr_block, module.check_mode)
        elif state == 'present':
            result = ensure_vpc_present(
                vpc_conn=vpc_conn,
                vpc_id=vpc_id,
                cidr_block=cidr_block,
                instance_tenancy=instance_tenancy,
                dns_support=dns_support,
                dns_hostnames=dns_hostnames,
                subnet_ids=subnet_ids,
                route_table_ids=route_table_ids,
                resource_tags=resource_tags,
                wait=wait,
                wait_timeout=wait_timeout,
                check_mode=module.check_mode,
            )
    except AnsibleVPCException as e:
        module.fail_json(msg=str(e))

    module.exit_json(**result)


from ansible.module_utils.basic import *  # noqa
from ansible.module_utils.ec2 import *  # noqa

if __name__ == '__main__':
    main()
