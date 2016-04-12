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

# This is a DOCUMENTATION stub specific to this module, it extends
# a documentation fragment located in ansible.utils.module_docs_fragments
import socket, sys, time, ConfigParser, csv, pprint, urllib2
from subprocess import Popen, PIPE, STDOUT
from math import log as ln
from cm_api.api_client import ApiResource
from cm_api.api_client import ApiException
from cm_api.endpoints.services import ApiService
from cm_api.endpoints.services import ApiServiceSetupInfo

DOCUMENTATION = '''
---
module: cloudera_init
short_description: create / delete / start /stop a Cloudera cluster
description:
     - creates / deletes / starts / stops  a Cloudera cluster using Cloudera Manager.
version_added: "2.1"
options:
  name:
    description:
      - Name to be given to the cluster
    default: null
  fullVersion:
    description:
      - Full version of the cluster
    default: 5.6.0
  admin_password:
    description:
      - Password of the admin account for the cluster
    default: admin
  cm_host:
    description:
      - Hostname of the node running Cloudera Manager
    default: localhost
  hosts:
    description:
      - Comma separated hostnames of the nodes forming the cluster
    default: null
  state:
    description:
      - Indicate desired state of the resource
    choices:
      - present
      - absent
      - started
      - stopped
    default: present
author:
  - Alexandru Anghel
  - David Grier
'''

EXAMPLES = '''
- name: Build a Cloudera cluster
  gather_facts: False
  hosts: local
  connection: local
  tasks:
    - name: Cloudera cluster create request
      local_action:
        module: cloudera_init
        name: my-test-cluster
        fullVersion: 5.6.0
        admin_password: admin
        cm_host: localhost
        hosts: localhost
        state: present
      register: my_cdh

    - debug: var=my_cdh
'''


def find_cluster(module, api, name):
    try:
        cluster = api.get_cluster(name)
        if not cluster:
            return None

    except ApiException as e:
        if e.code == 404:
            return None
        module.fail_json(msg='Failed to get cluster.\nError is %s' % e)

    return cluster


def init_cluster(module, api, name, fullVersion, hosts, cm_host):

    changed = False
    cluster = find_cluster(module, api, name)

    if not cluster:
        try:
            cluster = api.create_cluster(name, fullVersion=fullVersion)
            all_hosts = set(hosts.split(','))
            all_hosts.add(cm_host)
            cluster.add_hosts(all_hosts)
            changed = True
            time.sleep(10)
        except ApiException as e:
            module.fail_json(msg='Failed to build cluster.\nError is %s' % e)

    result = dict(changed=changed, cluster=cluster.name)
    module.exit_json(**result)

def start_cluster(module, api, name, fullVersion, hosts, cm_host):

    changed = False

    try:
        cluster = find_cluster(module, api, name)

    except ApiException as e:
        module.fail_json(msg='Failed to find cluster.\nError is %s' % e)

    try:
        cluster.start().wait()


    result = dict(changed=changed, cluster=cluster.name)
    module.exit_json(**result)

def stop_cluster(module, api, name, fullVersion, hosts, cm_host):

    changed = False

    try:
        cluster = find_cluster(module, api, name)

    except ApiException as e:
        module.fail_json(msg='Failed to find cluster.\nError is %s' % e)

    try:
        cluster.stop().wait()


    result = dict(changed=changed, cluster=cluster.name)
    module.exit_json(**result)

def finalize_startup(module, api, name, hdfs_service, oozie_service):

    changed = False
    cluster = find_cluster(module, api, name)

    # Create HDFS temp dir
    hdfs_service.create_hdfs_tmp()

    # Create hive warehouse dir
    shell_command = [
        'curl -i -H "Content-Type: application/json" -X POST -u "' + ADMIN_USER + ':' + ADMIN_PASS + '" -d "serviceName=' + HIVE_SERVICE_NAME + ';clusterName=' + CLUSTER_NAME + '" http://' + CM_HOST + ':7180/api/v5/clusters/' + CLUSTER_NAME + '/services/' + HIVE_SERVICE_NAME + '/commands/hiveCreateHiveWarehouse']
    create_hive_warehouse_output = Popen(shell_command, shell=True, stdin=PIPE, stdout=PIPE, stderr=STDOUT,
                                         close_fds=True).stdout.read()

    # Create oozie database
    oozie_service.stop().wait()
    shell_command = [
        'curl -i -H "Content-Type: application/json" -X POST -u "' + ADMIN_USER + ':' + ADMIN_PASS + '" -d "serviceName=' + OOZIE_SERVICE_NAME + ';clusterName=' + CLUSTER_NAME + '" http://' + CM_HOST + ':7180/api/v5/clusters/' + CLUSTER_NAME + '/services/' + OOZIE_SERVICE_NAME + '/commands/createOozieDb']
    create_oozie_db_output = Popen(shell_command, shell=True, stdin=PIPE, stdout=PIPE, stderr=STDOUT,
                                   close_fds=True).stdout.read()
    # give the create db command time to complete
    time.sleep(30)
    oozie_service.start().wait()

    # Deploy client configs to all necessary hosts
    cmd = cluster.deploy_client_config()
    if not cmd.wait(CMD_TIMEOUT).success:
        print "Failed to deploy client configs for {0}".format(cluster.name)

    # Noe change permissions on the /user dir so YARN will work
    shell_command = ['sudo -u hdfs hadoop fs -chmod 775 /user']
    user_chmod_output = Popen(shell_command, shell=True, stdin=PIPE, stdout=PIPE, stderr=STDOUT,
                              close_fds=True).stdout.read()

    result = dict(changed=changed, cluster=cluster.name)
    module.exit_json(**result)


def delete_cluster(module, api, name):

    changed = False
    cluster = find_cluster(module, api, name)
    if cluster:
        try:
            api.delete_cluster(name)
            changed = True
            time.sleep(5)
        except ApiException as e:
            module.fail_json(msg='Failed to delete cluster.\nError is %s' % e)
    else:
        module.fail_json(msg='Cluster does not exist.')

    result = dict(changed=changed, cluster=cluster.name)
    module.exit_json(**result)


def main():
    argument_spec = dict(
        name=dict(type='str'),
        fullVersion=dict(type='str', default='5.6.0'),
        admin_password=dict(type='str', default='admin'),
        state=dict(default='present', choices=['present', 'absent', 'started', 'stopped', 'finalize']),
        cm_host=dict(type='str', default='localhost'),
        hosts=dict(type='str', default=''),
        trial=dict(type='bool', default=False),
        wait=dict(type='bool', default=False),
        wait_timeout=dict(default=30)
    )

    module = AnsibleModule(
        argument_spec=argument_spec
    )

    name = module.params.get('name')
    fullVersion = module.params.get('fullVersion')
    admin_password = module.params.get('admin_password')
    state = module.params.get('state')
    cm_host = module.params.get('cm_host')
    hosts = module.params.get('hosts')
    trial = module.params.get('trial')
    wait = module.params.get('wait')
    wait_timeout = int(module.params.get('wait_timeout'))

    if not name:
        module.fail_json(msg='The cluster name is required for this module')

    cfg = ConfigParser.SafeConfigParser()

    try:
        API = ApiResource(cm_host, version=fullVersion[0], username="admin", password=admin_password)
        MANAGER = API.get_cloudera_manager()
        if trial:
            MANAGER.begin_trial()
    except ApiException as e:
        module.fail_json(msg='Failed to connect to Cloudera Manager.\nError is %s' % e)

    if state == "absent":
        delete_cluster(module, API, name)
    elif state == "present":
        init_cluster(module, API, name, fullVersion, hosts, cm_host)
    elif state == "started":
        start_cluster(module, API, name, fullVersion, hosts, cm_host)
    elif state == "finalize":
        finalize_startup(module, API, name, hdfs_service, oozie_service)
    else
        stop_cluster(module, API, name, fullVersion, hosts, cm_host)

    return cluster
# import module snippets
from ansible.module_utils.basic import *

### invoke the module
main()
