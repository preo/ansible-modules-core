#!/usr/bin/python
#
# (c) 2013, Nimbis Services
#
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
module: ec2_ami_search
short_description: Searches AWS for matching AMI.
deprecated: "in favor of the ec2_ami_find module"
version_added: "1.6"
description:
  - Look up the most recent AMI on AWS for a given operating system.
  - Returns C(ami), C(aki), C(ari), C(serial), C(tag)
  - If there is no AKI or ARI associated with an image, these will be C(null).
  - 'Example output: C({"ami": "ami-69f5a900", "changed": false, "aki":'''
''' "aki-88aa75e1", "ari": null})'
version_added: "1.6"
options:
  name:
    description: Image name pattern
    required: False
    default: None
  filters:
    description: Additional filters to pass to AWS (see'''
''' http://docs.aws.amazon.com/AWSEC2/latest/CommandLineReference'''
'''/ApiReference-cmd-DescribeImages.html)
    required: False
    default: None
  image_id:
    description: One or more image IDs to search specifically for.
    required: False
    default: None
  owner:
    description: One or more owner IDs to filter by. Also accepts 'ubuntu' '''
'''and 'amazon'.
    required: False
    default: None
  store:
    description: Back-end store for instance
    required: False
    default: None
    choices: ['ebs', 'ebs-io1', 'ebs-ssd', 'instance-store']
  arch:
    description: CPU architecture
    required: False
    default: None
    choices: ['i386', 'x86_64', 'amd64']
    aliases: ['architecture']
  virt:
    description: virtualization type
    required: False
    default: None
    choices: ['paravirtual', 'hvm']
  ec2_region:
    description: EC2 region
    required: True
    aliases: ['aws_region', 'ec2_region', 'region']
  aws_secret_key:
    description:
      - AWS secret key. If not set then the value of the AWS_SECRET_KEY'''
''' environment variable is used.
    required: False
    default: None
    aliases: ['ec2_secret_key', 'secret_key']
  aws_access_key:
    description:
      - AWS access key. If not set then the value of the AWS_ACCESS_KEY'''
''' environment variable is used.
    required: False
    default: None
    aliases: ['ec2_access_key', 'access_key']
  validate_certs:
    description:
      - When set to 'no', SSL certificates will not be validated for'''
''' boto versions >= 2.6.0.
    required: false
    default: 'yes'
    choices: ['yes', 'no']
    aliases: []

author: Lorin Hochstein
'''

EXAMPLES = '''
- name: Launch an Ubuntu 12.04 (Precise Pangolin) EC2 instance
  hosts: 127.0.0.1
  connection: local
  tasks:
  - name: Get the Ubuntu precise AMI
    ec2_ami_search:
        name='ubuntu/images*precise*'
        owner='ubuntu'
        region=us-west-1
        owner=ubuntu
        arch=x86_64
        store=instance-store
        virt=paravirtual
    register: ubuntu_image
  - name: Start the EC2 instance
    ec2: image={{ ubuntu_image.ami }} instance_type=m1.small key_name=mykey

- name: Find AWS NAT AMI
  hosts: 127.0.0.1
  connection: local
  tasks:
  - name: Find AWS NAT AMI
    ec2_ami_search
      name='amzn-ami-vpc-nat*'
      owner=amazon
      region=us-west-1
      arch=x86_64
      virt=paravirtual
    register: nat_ami
'''

import sys
import re

try:
    import boto.ec2
    from boto.exception import EC2ResponseError, NoAuthHandlerFound
except ImportError:
    print "failed=True msg='boto required for this module'"
    sys.exit(1)


def natural_sort_key(item):
    """ For images (like Ubuntu) who use YYYYMMDD or YYYYMMDD.N,
    this helps ensure sort order is correct to choose the latest image."""
    def convert(text):
        if text.isdigit():
            return int(text)
        return text.lower()
    return [convert(c) for c in re.split('([0-9]+)', item)]


def image_sort_key(image):
    return natural_sort_key(image.name)


def search_ami(ec2_conn, name, image_ids, owner_ids, arch, store, virt,
               filters):

    if filters is None:
        filters = {}

    if name is not None:
        filters['name'] = name

    filters['state'] = 'available'
    filters['image-type'] = 'machine'

    if store is not None:
        if store == 'instance-store':
            filters['root-device-type'] = 'instance-store'
        elif store == 'ebs':
            filters['root-device-type'] = 'ebs'
            filters['block-device-mapping.volume-type'] = 'standard'
        elif store == 'ebs-ssd':
            filters['root-device-type'] = 'ebs'
            filters['block-device-mapping.volume-type'] = 'gp2'
        elif store == 'ebs-io1':
            filters['root-device-type'] = 'ebs'
            filters['block-device-mapping.volume-type'] = 'io1'
        else:
            raise ValueError('Invalid instance store parameter {}'
                             .format(store))
    if arch is not None:
        filters['architecture'] = arch

    if virt is not None:
        filters['virtualization-type'] = virt

    images = ec2_conn.get_all_images(image_ids, owner_ids, filters=filters)
    return sorted(images, key=image_sort_key, reverse=True)


_KNOWN_OWNERS = {
    'ubuntu': '099720109477',
    'amazon': '137112412989',
}


def main():
    argument_spec = ec2_argument_spec()
    argument_spec.update(dict(
        name=dict(required=False),
        filters=dict(required=False, type='dict'),
        image_id=dict(required=False, type='list'),
        owner=dict(required=False, type='list'),
        architecture=dict(required=False, choices=['i386', 'x86_64', 'amd64'],
                          aliases=['arch']),
        store=dict(required=False,
                   choices=['ebs', 'ebs-io1', 'ebs-ssd', 'instance-store']),
        virt=dict(required=False, choices=['paravirtual', 'hvm']),
    ))
    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    owner_ids = module.params.get('owner')
    if owner_ids is not None:
        owner_ids = [_KNOWN_OWNERS.get(o, o) for o in owner_ids]

    image_ids = module.params.get('image_id')

    architecture = module.params.get('architecture')
    if architecture == 'amd64':
        architecture = 'x86_64'

    ec2_url, aws_access_key, aws_secret_key, region = get_ec2_creds(module)
    if not region:
        module.fail_json(msg='Region must be specified')

    try:
        ec2_conn = boto.ec2.connect_to_region(
            region,
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
        )
    except NoAuthHandlerFound, e:
        module.fail_json(msg=str(e))

    try:
        images = search_ami(
            ec2_conn,
            name=module.params.get('name'),
            image_ids=image_ids,
            owner_ids=owner_ids,
            arch=architecture,
            store=module.params.get('store'),
            virt=module.params.get('virt'),
            filters=module.params.get('filters'))
    except EC2ResponseError, e:
        module.fail_json(msg=str(e))

    if not images:
        module.fail_json(msg='No matching AMIs found')

    image = images[0]
    module.exit_json(**{
        'changed': False,
        'ami': image.id,
        'aki': image.kernel_id,
        'ari': image.virtualization_type,
        'image': {
            'id': image.id,
            'name': image.name,
            'kernel_id': image.kernel_id,
            'virtualization_type': image.virtualization_type,
            'description': image.description,

        }})

from ansible.module_utils.basic import *  # noqa
from ansible.module_utils.ec2 import *  # noqa

if __name__ == '__main__':
    main()
