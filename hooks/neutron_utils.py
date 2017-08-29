import os
import shutil
import subprocess
from shutil import copy2
from charmhelpers.core.host import (
    adduser,
    add_group,
    add_user_to_group,
    lsb_release,
    mkdir,
    service,
    service_running,
    service_stop,
    service_restart,
    write_file,
    init_is_systemd,
)
from charmhelpers.core.hookenv import (
    charm_dir,
    log,
    DEBUG,
    INFO,
    ERROR,
    config,
    relations_of_type,
    unit_private_ip,
    is_relation_made,
    relation_ids,
)
from charmhelpers.core.templating import render
from charmhelpers.fetch import (
    apt_upgrade,
    apt_update,
    apt_install,
)
from charmhelpers.contrib.network.ovs import (
    add_bridge,
    add_bridge_port,
    full_restart
)
from charmhelpers.contrib.hahelpers.cluster import (
    get_hacluster_config,
)
from charmhelpers.contrib.openstack.utils import (
    configure_installation_source,
    get_os_codename_install_source,
    git_clone_and_install,
    git_default_repos,
    git_generate_systemd_init_files,
    git_install_requested,
    git_pip_venv_dir,
    git_src_dir,
    get_hostname,
    make_assess_status_func,
    os_release,
    pause_unit,
    reset_os_release,
    resume_unit,
    os_application_version_set,
)

from charmhelpers.contrib.openstack.neutron import (
    determine_dkms_package
)

import charmhelpers.contrib.openstack.context as context
from charmhelpers.contrib.openstack.context import (
    SyslogContext,
    NeutronAPIContext,
    NetworkServiceContext,
    ExternalPortContext,
    PhyNICMTUContext,
    DataPortContext,
)
import charmhelpers.contrib.openstack.templating as templating
from charmhelpers.contrib.openstack.neutron import headers_package
from neutron_contexts import (
    CORE_PLUGIN, OVS, NSX, N1KV, OVS_ODL,
    NeutronGatewayContext,
    L3AgentContext,
    LoggingContext,
)
from charmhelpers.contrib.openstack.neutron import (
    parse_bridge_mappings,
)

from copy import deepcopy


def valid_plugin():
    return config('plugin') in CORE_PLUGIN

NEUTRON_COMMON = 'neutron-common'
VERSION_PACKAGE = NEUTRON_COMMON

NEUTRON_CONF_DIR = '/etc/neutron'

NEUTRON_ML2_PLUGIN_CONF = \
    "/etc/neutron/plugins/ml2/ml2_conf.ini"
NEUTRON_OVS_AGENT_CONF = \
    "/etc/neutron/plugins/ml2/openvswitch_agent.ini"
NEUTRON_NVP_PLUGIN_CONF = \
    "/etc/neutron/plugins/nicira/nvp.ini"
NEUTRON_NSX_PLUGIN_CONF = \
    "/etc/neutron/plugins/vmware/nsx.ini"


NEUTRON_PLUGIN_CONF = {
    OVS: NEUTRON_ML2_PLUGIN_CONF,
    NSX: NEUTRON_NSX_PLUGIN_CONF,
}

NEUTRON_DHCP_AA_PROFILE = 'usr.bin.neutron-dhcp-agent'
NEUTRON_L3_AA_PROFILE = 'usr.bin.neutron-l3-agent'
NEUTRON_LBAAS_AA_PROFILE = 'usr.bin.neutron-lbaas-agent'
NEUTRON_LBAASV2_AA_PROFILE = 'usr.bin.neutron-lbaasv2-agent'
NEUTRON_METADATA_AA_PROFILE = 'usr.bin.neutron-metadata-agent'
NEUTRON_METERING_AA_PROFILE = 'usr.bin.neutron-metering-agent'
NOVA_API_METADATA_AA_PROFILE = 'usr.bin.nova-api-metadata'
NEUTRON_OVS_AA_PROFILE = 'usr.bin.neutron-openvswitch-agent'

APPARMOR_PROFILES = [
    NEUTRON_DHCP_AA_PROFILE,
    NEUTRON_L3_AA_PROFILE,
    NEUTRON_LBAAS_AA_PROFILE,
    NEUTRON_METADATA_AA_PROFILE,
    NEUTRON_METERING_AA_PROFILE,
    NOVA_API_METADATA_AA_PROFILE,
    NEUTRON_OVS_AA_PROFILE
]

NEUTRON_OVS_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                               ''.format(NEUTRON_OVS_AA_PROFILE))
NEUTRON_DHCP_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                                ''.format(NEUTRON_DHCP_AA_PROFILE))
NEUTRON_L3_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                              ''.format(NEUTRON_L3_AA_PROFILE))
NEUTRON_LBAAS_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                                 ''.format(NEUTRON_LBAAS_AA_PROFILE))
NEUTRON_LBAASV2_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                                   ''.format(NEUTRON_LBAASV2_AA_PROFILE))
NEUTRON_METADATA_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                                    ''.format(NEUTRON_METADATA_AA_PROFILE))
NEUTRON_METERING_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                                    ''.format(NEUTRON_METERING_AA_PROFILE))
NOVA_API_METADATA_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                                     ''.format(NOVA_API_METADATA_AA_PROFILE))

GATEWAY_PKGS = {
    OVS: [
        "neutron-plugin-openvswitch-agent",
        "openvswitch-switch",
        "neutron-l3-agent",
        "neutron-dhcp-agent",
        'python-mysqldb',
        'python-psycopg2',
        'python-oslo.config',  # Force upgrade
        "nova-api-metadata",
        "neutron-metering-agent",
        "neutron-lbaas-agent",
    ],
    NSX: [
        "neutron-dhcp-agent",
        'python-mysqldb',
        'python-psycopg2',
        'python-oslo.config',  # Force upgrade
        "nova-api-metadata"
    ],
    N1KV: [
        "neutron-plugin-cisco",
        "neutron-dhcp-agent",
        "python-mysqldb",
        "python-psycopg2",
        "nova-api-metadata",
        "neutron-common",
        "neutron-l3-agent"
    ],
    OVS_ODL: [
        "openvswitch-switch",
        "neutron-l3-agent",
        "neutron-dhcp-agent",
        "nova-api-metadata",
        "neutron-metering-agent",
        "neutron-lbaas-agent",
    ],
}

EARLY_PACKAGES = {
    OVS: ['openvswitch-datapath-dkms'],
    NSX: [],
    N1KV: [],
    OVS_ODL: [],
}

LEGACY_HA_TEMPLATE_FILES = 'files'
LEGACY_FILES_MAP = {
    'neutron-ha-monitor.py': {
        'path': '/usr/local/bin/',
        'permissions': 0o755
    },
    'neutron-ha-monitor.conf': {
        'path': '/var/lib/juju-neutron-ha/',
    },
    'NeutronAgentMon': {
        'path': '/usr/lib/ocf/resource.d/canonical',
        'permissions': 0o755
    },
}
LEGACY_RES_MAP = ['res_monitor']
L3HA_PACKAGES = ['keepalived', 'conntrack']

BASE_GIT_PACKAGES = [
    'arping',
    'dnsmasq',
    'libffi-dev',
    'libssl-dev',
    'libxml2-dev',
    'libxslt1-dev',
    'libyaml-dev',
    'openstack-pkg-tools',
    'python-dev',
    'python-pip',
    'python-setuptools',
    'zlib1g-dev',
]

# ubuntu packages that should not be installed when deploying from git
GIT_PACKAGE_BLACKLIST = [
    'nova-api-metadata',
    'neutron-common',
    'neutron-dhcp-agent',
    'neutron-l3-agent',
    'neutron-lbaas-agent',
    'neutron-metadata-agent',
    'neutron-metering-agent',
    'neutron-plugin-cisco',
    'neutron-plugin-metering-agent',
    'neutron-plugin-openvswitch-agent',
    'neutron-openvswitch-agent',
    'neutron-vpn-agent',
    'python-neutron-fwaas',
    'python-oslo.config',
    'python-pymysql',
    'quantum-common',
    'quantum-dhcp-agent',
    'quantum-l3-agent',
    'quantum-metadata-agent',
    'quantum-plugin-openvswitch-agent',
]

# The interface is said to be satisfied if anyone of the interfaces in the
# list has a complete context.
REQUIRED_INTERFACES = {
    'messaging': ['amqp', 'zeromq-configuration'],
    'neutron-plugin-api': ['neutron-plugin-api'],
    'network-service': ['quantum-network-service'],
}


def get_early_packages():
    '''Return a list of package for pre-install based on configured plugin'''
    if config('plugin') in [OVS]:
        pkgs = determine_dkms_package()
    else:
        return []

    # ensure headers are installed build any required dkms packages
    if [p for p in pkgs if 'dkms' in p]:
        return pkgs + [headers_package()]
    return pkgs


def get_packages():
    '''Return a list of packages for install based on the configured plugin'''
    plugin = config('plugin')
    packages = deepcopy(GATEWAY_PKGS[plugin])
    source = os_release('neutron-common')
    if plugin == OVS:
        if (source >= 'icehouse' and
                lsb_release()['DISTRIB_CODENAME'] < 'utopic'):
            # NOTE(jamespage) neutron-vpn-agent supercedes l3-agent for
            # icehouse but openswan was removed in utopic.
            packages.remove('neutron-l3-agent')
            packages.append('neutron-vpn-agent')
            packages.append('openswan')
        if source >= 'liberty':
            # Switch out mysql driver
            packages.remove('python-mysqldb')
            packages.append('python-pymysql')
        if source >= 'mitaka':
            # Switch out to actual ovs agent package
            packages.remove('neutron-plugin-openvswitch-agent')
            packages.append('neutron-openvswitch-agent')
        if source >= 'kilo':
            packages.append('python-neutron-fwaas')
    if plugin in (OVS, OVS_ODL):
        if source >= 'newton':
            # LBaaS v1 dropped in newton
            packages.remove('neutron-lbaas-agent')
            packages.append('neutron-lbaasv2-agent')
    packages.extend(determine_l3ha_packages())

    if git_install_requested():
        packages = list(set(packages))
        packages.extend(BASE_GIT_PACKAGES)
        # don't include packages that will be installed from git
        for p in GIT_PACKAGE_BLACKLIST:
            if p in packages:
                packages.remove(p)

    return packages


def determine_l3ha_packages():
    if use_l3ha():
        return L3HA_PACKAGES
    return []


def use_l3ha():
    return NeutronAPIContext()()['enable_l3ha']

EXT_PORT_CONF = '/etc/init/ext-port.conf'
PHY_NIC_MTU_CONF = '/etc/init/os-charm-phy-nic-mtu.conf'
STOPPED_SERVICES = ['os-charm-phy-nic-mtu', 'ext-port']

TEMPLATES = 'templates'

QUANTUM_CONF = "/etc/quantum/quantum.conf"
QUANTUM_L3_AGENT_CONF = "/etc/quantum/l3_agent.ini"
QUANTUM_DHCP_AGENT_CONF = "/etc/quantum/dhcp_agent.ini"
QUANTUM_METADATA_AGENT_CONF = "/etc/quantum/metadata_agent.ini"

NEUTRON_CONF = "/etc/neutron/neutron.conf"
NEUTRON_L3_AGENT_CONF = "/etc/neutron/l3_agent.ini"
NEUTRON_DHCP_AGENT_CONF = "/etc/neutron/dhcp_agent.ini"
NEUTRON_DNSMASQ_CONF = "/etc/neutron/dnsmasq.conf"
NEUTRON_METADATA_AGENT_CONF = "/etc/neutron/metadata_agent.ini"
NEUTRON_METERING_AGENT_CONF = "/etc/neutron/metering_agent.ini"
NEUTRON_LBAAS_AGENT_CONF = "/etc/neutron/lbaas_agent.ini"
NEUTRON_VPNAAS_AGENT_CONF = "/etc/neutron/vpn_agent.ini"
NEUTRON_FWAAS_CONF = "/etc/neutron/fwaas_driver.ini"

NOVA_CONF_DIR = '/etc/nova'
NOVA_CONF = "/etc/nova/nova.conf"

NEUTRON_LOGGING_CONF = '%s/logging-neutron.conf' % NEUTRON_CONF_DIR
NOVA_LOGGING_CONF = '%s/logging-nova.conf' % NOVA_CONF_DIR

NOVA_CONFIG_FILES = {
    NOVA_CONF: {
        'hook_contexts': [NetworkServiceContext(),
                          NeutronGatewayContext(),
                          SyslogContext(),
                          context.WorkerConfigContext(),
                          context.ZeroMQContext(),
                          context.NotificationDriverContext()],
        'services': ['nova-api-metadata']
    },
    NOVA_API_METADATA_AA_PROFILE_PATH: {
        'services': ['nova-api-metadata'],
        'hook_contexts': [
            context.AppArmorContext(NOVA_API_METADATA_AA_PROFILE)
        ],
    },
    NOVA_LOGGING_CONF: {
        'services': ['nova-api-metadata'],
        'hook_contexts': [LoggingContext(),]
    }
}

NEUTRON_SHARED_CONFIG_FILES = {
    NEUTRON_DHCP_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-dhcp-agent']
    },
    NEUTRON_DNSMASQ_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-dhcp-agent']
    },
    NEUTRON_METADATA_AGENT_CONF: {
        'hook_contexts': [NetworkServiceContext(),
                          context.WorkerConfigContext(),
                          NeutronGatewayContext()],
        'services': ['neutron-metadata-agent']
    },
    NEUTRON_DHCP_AA_PROFILE_PATH: {
        'services': ['neutron-dhcp-agent'],
        'hook_contexts': [
            context.AppArmorContext(NEUTRON_DHCP_AA_PROFILE)
        ],
    },
    NEUTRON_LBAAS_AA_PROFILE_PATH: {
        'services': ['neutron-lbaas-agent'],
        'hook_contexts': [
            context.AppArmorContext(NEUTRON_LBAAS_AA_PROFILE)
        ],
    },
    NEUTRON_LBAASV2_AA_PROFILE_PATH: {
        'services': ['neutron-lbaasv2-agent'],
        'hook_contexts': [
            context.AppArmorContext(NEUTRON_LBAASV2_AA_PROFILE)
        ],
    },
    NEUTRON_METADATA_AA_PROFILE_PATH: {
        'services': ['neutron-metadata-agent'],
        'hook_contexts': [
            context.AppArmorContext(NEUTRON_METADATA_AA_PROFILE)
        ],
    },
    NEUTRON_METERING_AA_PROFILE_PATH: {
        'services': ['neutron-metering-agent'],
        'hook_contexts': [
            context.AppArmorContext(NEUTRON_METERING_AA_PROFILE)
        ],
    },
    NEUTRON_LOGGING_CONF: {
        'services': ['neutron-l3-agent',
                     'neutron-dhcp-agent',
                     'neutron-metadata-agent',
                     'neutron-plugin-openvswitch-agent',
                     'neutron-plugin-metering-agent',
                     'neutron-metering-agent',
                     'neutron-lbaas-agent',
                     'neutron-vpn-agent'],
        'hook_contexts': [LoggingContext(),]
    }
}
NEUTRON_SHARED_CONFIG_FILES.update(NOVA_CONFIG_FILES)

NEUTRON_OVS_CONFIG_FILES = {
    NEUTRON_CONF: {
        'hook_contexts': [context.AMQPContext(ssl_dir=NEUTRON_CONF_DIR),
                          NeutronGatewayContext(),
                          SyslogContext(),
                          context.ZeroMQContext(),
                          context.WorkerConfigContext(),
                          context.NotificationDriverContext()],
        'services': ['neutron-l3-agent',
                     'neutron-dhcp-agent',
                     'neutron-metadata-agent',
                     'neutron-plugin-openvswitch-agent',
                     'neutron-plugin-metering-agent',
                     'neutron-metering-agent',
                     'neutron-lbaas-agent',
                     'neutron-vpn-agent']
    },
    NEUTRON_L3_AGENT_CONF: {
        'hook_contexts': [NetworkServiceContext(),
                          L3AgentContext(),
                          NeutronGatewayContext()],
        'services': ['neutron-l3-agent', 'neutron-vpn-agent']
    },
    NEUTRON_METERING_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-plugin-metering-agent',
                     'neutron-metering-agent']
    },
    NEUTRON_LBAAS_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-lbaas-agent']
    },
    NEUTRON_VPNAAS_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-vpn-agent']
    },
    NEUTRON_FWAAS_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-l3-agent', 'neutron-vpn-agent']
    },
    NEUTRON_ML2_PLUGIN_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-plugin-openvswitch-agent']
    },
    NEUTRON_OVS_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-plugin-openvswitch-agent']
    },
    NEUTRON_OVS_AA_PROFILE_PATH: {
        'services': ['neutron-plugin-openvswitch-agent'],
        'hook_contexts': [
            context.AppArmorContext(NEUTRON_OVS_AA_PROFILE)
        ],
    },
    NEUTRON_L3_AA_PROFILE_PATH: {
        'services': ['neutron-l3-agent', 'neutron-vpn-agent'],
        'hook_contexts': [
            context.AppArmorContext(NEUTRON_L3_AA_PROFILE)
        ],
    },
    EXT_PORT_CONF: {
        'hook_contexts': [ExternalPortContext()],
        'services': ['ext-port']
    },
    PHY_NIC_MTU_CONF: {
        'hook_contexts': [PhyNICMTUContext()],
        'services': ['os-charm-phy-nic-mtu']
    }
}
NEUTRON_OVS_CONFIG_FILES.update(NEUTRON_SHARED_CONFIG_FILES)

NEUTRON_OVS_ODL_CONFIG_FILES = {
    NEUTRON_CONF: {
        'hook_contexts': [context.AMQPContext(ssl_dir=NEUTRON_CONF_DIR),
                          NeutronGatewayContext(),
                          SyslogContext(),
                          context.ZeroMQContext(),
                          context.WorkerConfigContext(),
                          context.NotificationDriverContext()],
        'services': ['neutron-l3-agent',
                     'neutron-dhcp-agent',
                     'neutron-metadata-agent',
                     'neutron-plugin-metering-agent',
                     'neutron-metering-agent',
                     'neutron-lbaas-agent',
                     'neutron-vpn-agent']
    },
    NEUTRON_L3_AGENT_CONF: {
        'hook_contexts': [NetworkServiceContext(),
                          L3AgentContext(),
                          NeutronGatewayContext()],
        'services': ['neutron-l3-agent', 'neutron-vpn-agent']
    },
    NEUTRON_METERING_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-plugin-metering-agent',
                     'neutron-metering-agent']
    },
    NEUTRON_LBAAS_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-lbaas-agent']
    },
    NEUTRON_VPNAAS_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-vpn-agent']
    },
    NEUTRON_FWAAS_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-l3-agent', 'neutron-vpn-agent']
    },
    EXT_PORT_CONF: {
        'hook_contexts': [ExternalPortContext()],
        'services': ['ext-port']
    },
    PHY_NIC_MTU_CONF: {
        'hook_contexts': [PhyNICMTUContext()],
        'services': ['os-charm-phy-nic-mtu']
    }
}
NEUTRON_OVS_ODL_CONFIG_FILES.update(NEUTRON_SHARED_CONFIG_FILES)

NEUTRON_NSX_CONFIG_FILES = {
    NEUTRON_CONF: {
        'hook_contexts': [context.AMQPContext(ssl_dir=NEUTRON_CONF_DIR),
                          NeutronGatewayContext(),
                          context.WorkerConfigContext(),
                          SyslogContext()],
        'services': ['neutron-dhcp-agent', 'neutron-metadata-agent']
    },
}
NEUTRON_NSX_CONFIG_FILES.update(NEUTRON_SHARED_CONFIG_FILES)

NEUTRON_N1KV_CONFIG_FILES = {
    NEUTRON_CONF: {
        'hook_contexts': [context.AMQPContext(ssl_dir=NEUTRON_CONF_DIR),
                          NeutronGatewayContext(),
                          context.WorkerConfigContext(),
                          SyslogContext()],
        'services': ['neutron-l3-agent',
                     'neutron-dhcp-agent',
                     'neutron-metadata-agent']
    },
    NEUTRON_L3_AGENT_CONF: {
        'hook_contexts': [NetworkServiceContext(),
                          L3AgentContext(),
                          NeutronGatewayContext()],
        'services': ['neutron-l3-agent']
    },
}
NEUTRON_N1KV_CONFIG_FILES.update(NEUTRON_SHARED_CONFIG_FILES)

CONFIG_FILES = {
    NSX: NEUTRON_NSX_CONFIG_FILES,
    OVS: NEUTRON_OVS_CONFIG_FILES,
    N1KV: NEUTRON_N1KV_CONFIG_FILES,
    OVS_ODL: NEUTRON_OVS_ODL_CONFIG_FILES
}

SERVICE_RENAMES = {
    'icehouse': {
        'neutron-plugin-metering-agent': 'neutron-metering-agent',
    },
    'mitaka': {
        'neutron-plugin-openvswitch-agent': 'neutron-openvswitch-agent',
    },
}


# Override file for systemd
SYSTEMD_NOVA_OVERRIDE = (
    '/etc/systemd/system/nova-api-metadata.service.d/override.conf'
)


def install_systemd_override():
    '''
    Install systemd override files for nova-api-metadata
    and reload systemd daemon if required.
    '''
    if init_is_systemd() and not os.path.exists(SYSTEMD_NOVA_OVERRIDE):
        mkdir(os.path.dirname(SYSTEMD_NOVA_OVERRIDE))
        shutil.copy(os.path.join('files',
                                 os.path.basename(SYSTEMD_NOVA_OVERRIDE)),
                    SYSTEMD_NOVA_OVERRIDE)
        subprocess.check_call(['systemctl', 'daemon-reload'])


def remap_service(service_name):
    '''
    Remap service names based on openstack release to deal
    with changes to packaging

    :param service_name: name of service to remap
    :returns: remapped service name or original value
    '''
    source = os_release('neutron-common')
    for rename_source in SERVICE_RENAMES:
        if (source >= rename_source and
                service_name in SERVICE_RENAMES[rename_source]):
            service_name = SERVICE_RENAMES[rename_source][service_name]
    return service_name


def resolve_config_files(plugin, release):
    '''
    Resolve configuration files and contexts

    :param plugin: shortname of plugin e.g. ovs
    :param release: openstack release codename
    :returns: dict of configuration files, contexts
              and associated services
    '''
    config_files = deepcopy(CONFIG_FILES)
    drop_config = []
    if plugin == OVS:
        # NOTE: deal with switch to ML2 plugin for >= icehouse
        drop_config = [NEUTRON_OVS_AGENT_CONF]
        if release >= 'mitaka':
            # ml2 -> ovs_agent
            drop_config = [NEUTRON_ML2_PLUGIN_CONF]

    # Use MAAS1.9 for MTU and external port config on xenial and above
    if lsb_release()['DISTRIB_CODENAME'] >= 'xenial':
        drop_config.extend([EXT_PORT_CONF, PHY_NIC_MTU_CONF])

    # Rename to lbaasv2 in newton
    if os_release('neutron-common') < 'newton':
        drop_config.extend([NEUTRON_LBAASV2_AA_PROFILE_PATH])
    else:
        drop_config.extend([NEUTRON_LBAAS_AA_PROFILE_PATH])

    for _config in drop_config:
        if _config in config_files[plugin]:
            config_files[plugin].pop(_config)

    if is_relation_made('amqp-nova'):
        amqp_nova_ctxt = context.AMQPContext(
            ssl_dir=NOVA_CONF_DIR,
            rel_name='amqp-nova',
            relation_prefix='nova')
    else:
        amqp_nova_ctxt = context.AMQPContext(
            ssl_dir=NOVA_CONF_DIR,
            rel_name='amqp')
    config_files[plugin][NOVA_CONF][
        'hook_contexts'].append(amqp_nova_ctxt)
    return config_files


def register_configs():
    ''' Register config files with their respective contexts. '''
    release = os_release('neutron-common')
    plugin = config('plugin')
    config_files = resolve_config_files(plugin, release)
    configs = templating.OSConfigRenderer(templates_dir=TEMPLATES,
                                          openstack_release=release)
    for conf in config_files[plugin]:
        configs.register(conf,
                         config_files[plugin][conf]['hook_contexts'])
    return configs


def stop_services():
    release = os_release('neutron-common')
    plugin = config('plugin')
    config_files = resolve_config_files(plugin, release)
    svcs = set()
    for ctxt in config_files[config('plugin')].itervalues():
        for svc in ctxt['services']:
            svcs.add(remap_service(svc))
    for svc in svcs:
        service_stop(svc)


def restart_map():
    '''
    Determine the correct resource map to be passed to
    charmhelpers.core.restart_on_change() based on the services configured.

    :returns: dict: A dictionary mapping config file to lists of services
                    that should be restarted when file changes.
    '''
    release = os_release('neutron-common')
    plugin = config('plugin')
    config_files = resolve_config_files(plugin, release)
    _map = {}
    enable_vpn_agent = 'neutron-vpn-agent' in get_packages()
    for f, ctxt in config_files[plugin].iteritems():
        svcs = set()
        for svc in ctxt['services']:
            svcs.add(remap_service(svc))
        if not enable_vpn_agent and 'neutron-vpn-agent' in svcs:
            svcs.remove('neutron-vpn-agent')
        if 'neutron-vpn-agent' in svcs and 'neutron-l3-agent' in svcs:
            svcs.remove('neutron-l3-agent')
        if release >= 'newton' and 'neutron-lbaas-agent' in svcs:
            svcs.remove('neutron-lbaas-agent')
            svcs.add('neutron-lbaasv2-agent')
        if svcs:
            _map[f] = list(svcs)
    return _map


INT_BRIDGE = "br-int"
EXT_BRIDGE = "br-ex"

DHCP_AGENT = "DHCP Agent"
L3_AGENT = "L3 Agent"


# TODO: make work with neutron
def reassign_agent_resources():
    ''' Use agent scheduler API to detect down agents and re-schedule '''
    env = NetworkServiceContext()()
    if not env:
        log('Unable to re-assign resources at this time')
        return
    try:
        from quantumclient.v2_0 import client
    except ImportError:
        ''' Try to import neutronclient instead for havana+ '''
        from neutronclient.v2_0 import client

    auth_url = '%(auth_protocol)s://%(keystone_host)s:%(auth_port)s/v2.0' % env
    quantum = client.Client(username=env['service_username'],
                            password=env['service_password'],
                            tenant_name=env['service_tenant'],
                            auth_url=auth_url,
                            region_name=env['region'])

    partner_gateways = [unit_private_ip().split('.')[0]]
    for partner_gateway in relations_of_type(reltype='cluster'):
        gateway_hostname = get_hostname(partner_gateway['private-address'])
        partner_gateways.append(gateway_hostname.partition('.')[0])

    agents = quantum.list_agents(agent_type=DHCP_AGENT)
    dhcp_agents = []
    l3_agents = []
    networks = {}
    for agent in agents['agents']:
        if not agent['alive']:
            log('DHCP Agent %s down' % agent['id'])
            for network in \
                    quantum.list_networks_on_dhcp_agent(
                        agent['id'])['networks']:
                networks[network['id']] = agent['id']
        else:
            if agent['host'].partition('.')[0] in partner_gateways:
                dhcp_agents.append(agent['id'])

    agents = quantum.list_agents(agent_type=L3_AGENT)
    routers = {}
    for agent in agents['agents']:
        if not agent['alive']:
            log('L3 Agent %s down' % agent['id'])
            for router in \
                    quantum.list_routers_on_l3_agent(
                        agent['id'])['routers']:
                routers[router['id']] = agent['id']
        else:
            if agent['host'].split('.')[0] in partner_gateways:
                l3_agents.append(agent['id'])

    if len(dhcp_agents) == 0 or len(l3_agents) == 0:
        log('Unable to relocate resources, there are %s dhcp_agents and %s \
             l3_agents in this cluster' % (len(dhcp_agents), len(l3_agents)))
        return

    index = 0
    for router_id in routers:
        agent = index % len(l3_agents)
        log('Moving router %s from %s to %s' %
            (router_id, routers[router_id], l3_agents[agent]))
        quantum.remove_router_from_l3_agent(l3_agent=routers[router_id],
                                            router_id=router_id)
        quantum.add_router_to_l3_agent(l3_agent=l3_agents[agent],
                                       body={'router_id': router_id})
        index += 1

    index = 0
    for network_id in networks:
        agent = index % len(dhcp_agents)
        log('Moving network %s from %s to %s' %
            (network_id, networks[network_id], dhcp_agents[agent]))
        quantum.remove_network_from_dhcp_agent(dhcp_agent=networks[network_id],
                                               network_id=network_id)
        quantum.add_network_to_dhcp_agent(dhcp_agent=dhcp_agents[agent],
                                          body={'network_id': network_id})
        index += 1


def services():
    ''' Returns a list of services associate with this charm '''
    _services = []
    for v in restart_map().values():
        _services = _services + v
    return list(set(_services))


def do_openstack_upgrade(configs):
    """
    Perform an upgrade.  Takes care of upgrading packages, rewriting
    configs, database migrations and potentially any other post-upgrade
    actions.
    """
    new_src = config('openstack-origin')
    new_os_rel = get_os_codename_install_source(new_src)
    log('Performing OpenStack upgrade to %s.' % (new_os_rel))

    configure_installation_source(new_src)
    dpkg_opts = [
        '--option', 'Dpkg::Options::=--force-confnew',
        '--option', 'Dpkg::Options::=--force-confdef',
    ]
    apt_update(fatal=True)
    apt_upgrade(options=dpkg_opts,
                fatal=True, dist=True)
    # The cached version of os_release will now be invalid as the pkg version
    # should have changed during the upgrade.
    reset_os_release()
    apt_install(get_early_packages(), fatal=True)
    apt_install(get_packages(), fatal=True)
    configs.set_release(openstack_release=new_os_rel)
    configs.write_all()


def configure_ovs():
    if config('plugin') in [OVS, OVS_ODL]:
        if not service_running('openvswitch-switch'):
            full_restart()
        add_bridge(INT_BRIDGE)
        add_bridge(EXT_BRIDGE)
        ext_port_ctx = ExternalPortContext()()
        if ext_port_ctx and ext_port_ctx['ext_port']:
            add_bridge_port(EXT_BRIDGE, ext_port_ctx['ext_port'])

        portmaps = DataPortContext()()
        bridgemaps = parse_bridge_mappings(config('bridge-mappings'))
        for provider, br in bridgemaps.iteritems():
            add_bridge(br)
            if not portmaps:
                continue

            for port, _br in portmaps.iteritems():
                if _br == br:
                    add_bridge_port(br, port, promisc=True)

        # Ensure this runs so that mtu is applied to data-port interfaces if
        # provided.
        service_restart('os-charm-phy-nic-mtu')


def copy_file(src, dst, perms=None, force=False):
    """Copy file to destination and optionally set permissionss.

    If destination does not exist it will be created.
    """
    if not os.path.isdir(dst):
        log('Creating directory %s' % dst, level=DEBUG)
        mkdir(dst)

    fdst = os.path.join(dst, os.path.basename(src))
    if not os.path.isfile(fdst) or force:
        try:
            copy2(src, fdst)
            if perms:
                os.chmod(fdst, perms)
        except IOError:
            log('Failed to copy file from %s to %s.' % (src, dst), level=ERROR)
            raise


def remove_file(path):
    if not os.path.isfile(path):
        log('File %s does not exist.' % path, level=INFO)
        return

    try:
        os.remove(path)
    except IOError:
        log('Failed to remove file %s.' % path, level=ERROR)


def install_legacy_ha_files(force=False):
    for f, p in LEGACY_FILES_MAP.iteritems():
        srcfile = os.path.join(LEGACY_HA_TEMPLATE_FILES, f)
        copy_file(srcfile, p['path'], p.get('permissions', None), force=force)


def remove_legacy_ha_files():
    for f, p in LEGACY_FILES_MAP.iteritems():
        remove_file(os.path.join(p['path'], f))


def update_legacy_ha_files(force=False):
    if config('ha-legacy-mode'):
        install_legacy_ha_files(force=force)
    else:
        remove_legacy_ha_files()


def cache_env_data():
    env = NetworkServiceContext()()
    if not env:
        log('Unable to get NetworkServiceContext at this time', level=ERROR)
        return

    no_envrc = False
    envrc_f = '/etc/legacy_ha_envrc'
    if os.path.isfile(envrc_f):
        with open(envrc_f, 'r') as f:
            data = f.read()

        data = data.strip().split('\n')
        diff = False
        for line in data:
            k = line.split('=')[0]
            v = line.split('=')[1]
            if k not in env or v != env[k]:
                diff = True
                break
    else:
        no_envrc = True

    if no_envrc or diff:
        with open(envrc_f, 'w') as f:
            for k, v in env.items():
                f.write(''.join([k, '=', v, '\n']))


def stop_neutron_ha_monitor_daemon():
    try:
        cmd = ['pgrep', '-f', 'neutron-ha-monitor.py']
        res = subprocess.check_output(cmd).decode('UTF-8')
        pid = res.strip()
        if pid:
            subprocess.call(['sudo', 'kill', '-9', pid])
    except subprocess.CalledProcessError as e:
        log('Faild to kill neutron-ha-monitor daemon, %s' % e, level=ERROR)


def cleanup_ovs_netns():
    try:
        subprocess.call('neutron-ovs-cleanup')
        subprocess.call('neutron-netns-cleanup')
    except subprocess.CalledProcessError as e:
        log('Faild to cleanup ovs and netns, %s' % e, level=ERROR)


def get_topics():
    # metering_agent
    topics = []
    if 'neutron-l3-agent' in services():
        topics.append('l3_agent')
    if 'neutron-dhcp-agent' in services():
        topics.append('dhcp_agent')
    if 'neutron-metering-agent' in services():
        topics.append('metering_agent')
    if 'neutron-lbaas-agent' in services():
        topics.append('n-lbaas_agent')
    if 'neutron-plugin-openvswitch-agent' in services():
        topics.append('q-agent-notifier-port-update')
        topics.append('q-agent-notifier-network-delete')
        topics.append('q-agent-notifier-tunnel-update')
        topics.append('q-agent-notifier-security_group-update')
        topics.append('q-agent-notifier-dvr-update')
    topics.append('q-agent-notifier-l2population-update')
    return topics


def git_install(projects_yaml):
    """Perform setup, and install git repos specified in yaml parameter."""
    if git_install_requested():
        git_pre_install()
        projects_yaml = git_default_repos(projects_yaml)
        git_clone_and_install(projects_yaml, core_project='neutron')
        git_post_install(projects_yaml)


def git_pre_install():
    """Perform pre-install setup."""
    dirs = [
        '/etc/neutron',
        '/etc/neutron/rootwrap.d',
        '/etc/neutron/plugins',
        '/etc/nova',
        '/var/lib/neutron',
        '/var/lib/neutron/lock',
        '/var/log/neutron',
        '/var/lib/nova',
        '/var/log/nova',
    ]

    logs = [
        '/var/log/neutron/bigswitch-agent.log',
        '/var/log/neutron/dhcp-agent.log',
        '/var/log/neutron/l3-agent.log',
        '/var/log/neutron/lbaas-agent.log',
        '/var/log/neutron/ibm-agent.log',
        '/var/log/neutron/linuxbridge-agent.log',
        '/var/log/neutron/metadata-agent.log',
        '/var/log/neutron/metering_agent.log',
        '/var/log/neutron/mlnx-agent.log',
        '/var/log/neutron/nec-agent.log',
        '/var/log/neutron/nvsd-agent.log',
        '/var/log/neutron/openflow-agent.log',
        '/var/log/neutron/openvswitch-agent.log',
        '/var/log/neutron/ovs-cleanup.log',
        '/var/log/neutron/ryu-agent.log',
        '/var/log/neutron/server.log',
        '/var/log/neutron/sriov-agent.log',
        '/var/log/neutron/vpn_agent.log',
    ]

    adduser('neutron', shell='/bin/bash', system_user=True)
    add_group('neutron', system_group=True)
    add_user_to_group('neutron', 'neutron')

    adduser('nova', shell='/bin/bash', system_user=True)
    subprocess.check_call(['usermod', '--home', '/var/lib/nova', 'nova'])
    add_group('nova', system_group=True)
    add_user_to_group('nova', 'nova')

    for d in dirs:
        mkdir(d, owner='neutron', group='neutron', perms=0o755, force=False)

    for l in logs:
        write_file(l, '', owner='neutron', group='neutron', perms=0o644)


def git_post_install(projects_yaml):
    """Perform post-install setup."""
    etc_neutron = os.path.join(git_src_dir(projects_yaml, 'neutron'), 'etc')
    etc_nova = os.path.join(git_src_dir(projects_yaml, 'nova'), 'etc/nova')
    configs = [
        {'src': etc_neutron,
         'dest': '/etc/neutron'},
        {'src': os.path.join(etc_neutron, 'neutron/plugins'),
         'dest': '/etc/neutron/plugins'},
        {'src': os.path.join(etc_neutron, 'neutron/rootwrap.d'),
         'dest': '/etc/neutron/rootwrap.d'},
        {'src': etc_nova,
         'dest': '/etc/nova'},
        {'src': os.path.join(etc_nova, 'rootwrap.d'),
         'dest': '/etc/nova/rootwrap.d'},
    ]

    for c in configs:
        if os.path.exists(c['dest']):
            shutil.rmtree(c['dest'])
        shutil.copytree(c['src'], c['dest'])

    # NOTE(coreycb): Need to find better solution than bin symlinks.
    symlinks = [
        {'src': os.path.join(git_pip_venv_dir(projects_yaml),
                             'bin/neutron-ns-metadata-proxy'),
         'link': '/usr/local/bin/neutron-ns-metadata-proxy'},
        {'src': os.path.join(git_pip_venv_dir(projects_yaml),
                             'bin/neutron-rootwrap'),
         'link': '/usr/local/bin/neutron-rootwrap'},
        {'src': '/usr/local/bin/neutron-rootwrap',
         'link': '/usr/bin/neutron-rootwrap'},
        {'src': os.path.join(git_pip_venv_dir(projects_yaml),
                             'bin/nova-rootwrap'),
         'link': '/usr/local/bin/nova-rootwrap'},
        {'src': os.path.join(git_pip_venv_dir(projects_yaml),
                             'bin/nova-rootwrap-daemon'),
         'link': '/usr/local/bin/nova-rootwrap-daemon'},
    ]

    for s in symlinks:
        if os.path.lexists(s['link']):
            os.remove(s['link'])
        os.symlink(s['src'], s['link'])

    render('git/neutron_sudoers',
           '/etc/sudoers.d/neutron_sudoers', {}, perms=0o440)
    render('git/nova_sudoers',
           '/etc/sudoers.d/nova_sudoers', {}, perms=0o440)
    render('git/cron.d/neutron-dhcp-agent-netns-cleanup',
           '/etc/cron.d/neutron-dhcp-agent-netns-cleanup', {}, perms=0o755)
    render('git/cron.d/neutron-l3-agent-netns-cleanup',
           '/etc/cron.d/neutron-l3-agent-netns-cleanup', {}, perms=0o755)
    render('git/cron.d/neutron-lbaas-agent-netns-cleanup',
           '/etc/cron.d/neutron-lbaas-agent-netns-cleanup', {}, perms=0o755)

    bin_dir = os.path.join(git_pip_venv_dir(projects_yaml), 'bin')
    # Use systemd init units/scripts from ubuntu wily onward
    if lsb_release()['DISTRIB_RELEASE'] >= '15.10':
        templates_dir = os.path.join(charm_dir(), 'templates/git')
        daemons = ['neutron-dhcp-agent', 'neutron-l3-agent',
                   'neutron-lbaasv2-agent',
                   'neutron-linuxbridge-agent', 'neutron-linuxbridge-cleanup',
                   'neutron-macvtap-agent', 'neutron-metadata-agent',
                   'neutron-metering-agent', 'neutron-openvswitch-agent',
                   'neutron-ovs-cleanup', 'neutron-server',
                   'neutron-sriov-nic-agent', 'neutron-vpn-agent',
                   'nova-api-metadata']
        if os_release('neutron-common') <= 'mitaka':
            daemons.append('neutron-lbaas-agent')
        for daemon in daemons:
            neutron_context = {
                'daemon_path': os.path.join(bin_dir, daemon),
            }
            filename = daemon
            if daemon == 'neutron-sriov-nic-agent':
                filename = 'neutron-sriov-agent'
            elif daemon == 'neutron-openvswitch-agent':
                if os_release('neutron-common') < 'mitaka':
                    filename = 'neutron-plugin-openvswitch-agent'
            template_file = 'git/{}.init.in.template'.format(filename)
            init_in_file = '{}.init.in'.format(filename)
            render(template_file, os.path.join(templates_dir, init_in_file),
                   neutron_context, perms=0o644)
        git_generate_systemd_init_files(templates_dir)

        for daemon in daemons:
            filename = daemon
            if daemon == 'neutron-openvswitch-agent':
                if os_release('neutron-common') < 'mitaka':
                    filename = 'neutron-plugin-openvswitch-agent'
                service('enable', filename)
    else:
        service_name = 'quantum-gateway'
        user_name = 'neutron'
        neutron_api_context = {
            'service_description': 'Neutron API server',
            'service_name': service_name,
            'process_name': 'neutron-server',
            'executable_name': os.path.join(bin_dir, 'neutron-server'),
        }
        neutron_dhcp_agent_context = {
            'service_description': 'Neutron DHCP Agent',
            'service_name': service_name,
            'process_name': 'neutron-dhcp-agent',
            'executable_name': os.path.join(bin_dir, 'neutron-dhcp-agent'),
            'config_files': ['/etc/neutron/neutron.conf',
                             '/etc/neutron/dhcp_agent.ini'],
            'log_file': '/var/log/neutron/dhcp-agent.log',
        }
        neutron_l3_agent_context = {
            'service_description': 'Neutron L3 Agent',
            'service_name': service_name,
            'process_name': 'neutron-l3-agent',
            'executable_name': os.path.join(bin_dir, 'neutron-l3-agent'),
            'config_files': ['/etc/neutron/neutron.conf',
                             '/etc/neutron/l3_agent.ini',
                             '/etc/neutron/fwaas_driver.ini'],
            'log_file': '/var/log/neutron/l3-agent.log',
        }
        neutron_lbaas_agent_context = {
            'service_description': 'Neutron LBaaS Agent',
            'service_name': service_name,
            'user_name': user_name,
            'start_dir': '/var/lib/neutron',
            'process_name': 'neutron-lbaas-agent',
            'executable_name': os.path.join(bin_dir, 'neutron-lbaas-agent'),
            'config_files': ['/etc/neutron/neutron.conf',
                             '/etc/neutron/lbaas_agent.ini'],
            'log_file': '/var/log/neutron/lbaas-agent.log',
        }
        neutron_metadata_agent_context = {
            'service_description': 'Neutron Metadata Agent',
            'service_name': service_name,
            'user_name': user_name,
            'start_dir': '/var/lib/neutron',
            'process_name': 'neutron-metadata-agent',
            'executable_name': os.path.join(bin_dir, 'neutron-metadata-agent'),
            'config_files': ['/etc/neutron/neutron.conf',
                             '/etc/neutron/metadata_agent.ini'],
            'log_file': '/var/log/neutron/metadata-agent.log',
        }
        neutron_metering_agent_context = {
            'service_description': 'Neutron Metering Agent',
            'service_name': service_name,
            'user_name': user_name,
            'start_dir': '/var/lib/neutron',
            'process_name': 'neutron-metering-agent',
            'executable_name': os.path.join(bin_dir, 'neutron-metering-agent'),
            'config_files': ['/etc/neutron/neutron.conf',
                             '/etc/neutron/metering_agent.ini'],
            'log_file': '/var/log/neutron/metering-agent.log',
        }
        neutron_ovs_cleanup_context = {
            'service_description': 'Neutron OVS cleanup',
            'service_name': service_name,
            'user_name': user_name,
            'start_dir': '/var/lib/neutron',
            'process_name': 'neutron-ovs-cleanup',
            'executable_name': os.path.join(bin_dir, 'neutron-ovs-cleanup'),
            'config_file': '/etc/neutron/neutron.conf',
            'log_file': '/var/log/neutron/ovs-cleanup.log',
        }
        neutron_plugin_bigswitch_context = {
            'service_description': 'Neutron BigSwitch Plugin Agent',
            'service_name': service_name,
            'user_name': user_name,
            'start_dir': '/var/lib/neutron',
            'process_name': 'neutron-restproxy-agent',
            'executable_name': os.path.join(bin_dir,
                                            'neutron-restproxy-agent'),
            'config_files': ['/etc/neutron/neutron.conf',
                             '/etc/neutron/plugins/bigswitch/restproxy.ini'],
            'log_file': '/var/log/neutron/bigswitch-agent.log',
        }
        neutron_plugin_ibm_context = {
            'service_description': 'Neutron IBM SDN Plugin Agent',
            'service_name': service_name,
            'user_name': user_name,
            'start_dir': '/var/lib/neutron',
            'process_name': 'neutron-ibm-agent',
            'executable_name': os.path.join(bin_dir, 'neutron-ibm-agent'),
            'config_files':
                ['/etc/neutron/neutron.conf',
                 '/etc/neutron/plugins/ibm/sdnve_neutron_plugin.ini'],
            'log_file': '/var/log/neutron/ibm-agent.log',
        }
        neutron_plugin_linuxbridge_context = {
            'service_description': 'Neutron Linux Bridge Plugin Agent',
            'service_name': service_name,
            'user_name': user_name,
            'start_dir': '/var/lib/neutron',
            'process_name': 'neutron-linuxbridge-agent',
            'executable_name': os.path.join(bin_dir,
                                            'neutron-linuxbridge-agent'),
            'config_files': ['/etc/neutron/neutron.conf',
                             '/etc/neutron/plugins/ml2/ml2_conf.ini'],
            'log_file': '/var/log/neutron/linuxbridge-agent.log',
        }
        neutron_plugin_mlnx_context = {
            'service_description': 'Neutron MLNX Plugin Agent',
            'service_name': service_name,
            'user_name': user_name,
            'start_dir': '/var/lib/neutron',
            'process_name': 'neutron-mlnx-agent',
            'executable_name': os.path.join(bin_dir, 'neutron-mlnx-agent'),
            'config_files': ['/etc/neutron/neutron.conf',
                             '/etc/neutron/plugins/mlnx/mlnx_conf.ini'],
            'log_file': '/var/log/neutron/mlnx-agent.log',
        }
        neutron_plugin_nec_context = {
            'service_description': 'Neutron NEC Plugin Agent',
            'service_name': service_name,
            'start_dir': '/var/lib/neutron',
            'process_name': 'neutron-nec-agent',
            'executable_name': os.path.join(bin_dir, 'neutron-nec-agent'),
            'config_files': ['/etc/neutron/neutron.conf',
                             '/etc/neutron/plugins/nec/nec.ini'],
            'log_file': '/var/log/neutron/nec-agent.log',
        }
        neutron_plugin_oneconvergence_context = {
            'service_description': 'Neutron One Convergence Plugin Agent',
            'service_name': service_name,
            'user_name': user_name,
            'start_dir': '/var/lib/neutron',
            'process_name': 'neutron-nvsd-agent',
            'executable_name': os.path.join(bin_dir, 'neutron-nvsd-agent'),
            'config_files':
                ['/etc/neutron/neutron.conf',
                 '/etc/neutron/plugins/oneconvergence/nvsdplugin.ini'],
            'log_file': '/var/log/neutron/nvsd-agent.log',
        }
        neutron_plugin_openflow_context = {
            'service_description': 'Neutron OpenFlow Plugin Agent',
            'service_name': service_name,
            'user_name': user_name,
            'start_dir': '/var/lib/neutron',
            'process_name': 'neutron-ofagent-agent',
            'executable_name': os.path.join(bin_dir, 'neutron-ofagent-agent'),
            'config_files': ['/etc/neutron/neutron.conf',
                             '/etc/neutron/plugins/ml2/ml2_conf_ofa.ini'],
            'log_file': '/var/log/neutron/openflow-agent.log',
        }
        neutron_plugin_openvswitch_context = {
            'service_description': 'Neutron OpenvSwitch Plugin Agent',
            'service_name': service_name,
            'user_name': user_name,
            'start_dir': '/var/lib/neutron',
            'process_name': 'neutron-openvswitch-agent',
            'executable_name': os.path.join(bin_dir,
                                            'neutron-openvswitch-agent'),
            'config_files': ['/etc/neutron/neutron.conf',
                             '/etc/neutron/plugins/ml2/ml2_conf.ini'],
            'log_file': '/var/log/neutron/openvswitch-agent.log',
        }
        neutron_plugin_ryu_context = {
            'service_description': 'Neutron RYU Plugin Agent',
            'service_name': service_name,
            'user_name': user_name,
            'start_dir': '/var/lib/neutron',
            'process_name': 'neutron-ryu-agent',
            'executable_name': os.path.join(bin_dir, 'neutron-ryu-agent'),
            'config_files': ['/etc/neutron/neutron.conf',
                             '/etc/neutron/plugins/ryu/ryu.ini'],
            'log_file': '/var/log/neutron/ryu-agent.log',
        }
        neutron_plugin_sriov_context = {
            'service_description': 'Neutron SRIOV SDN Plugin Agent',
            'service_name': service_name,
            'user_name': user_name,
            'start_dir': '/var/lib/neutron',
            'process_name': 'neutron-sriov-nic-agent',
            'executable_name': os.path.join(bin_dir,
                                            'neutron-sriov-nic-agent'),
            'config_files': ['/etc/neutron/neutron.conf',
                             '/etc/neutron/plugins/ml2/ml2_conf_sriov'],
            'log_file': '/var/log/neutron/sriov-agent.log',
        }
        neutron_vpn_agent_context = {
            'service_description': 'Neutron VPN Agent',
            'service_name': service_name,
            'process_name': 'neutron-vpn-agent',
            'executable_name': os.path.join(bin_dir, 'neutron-vpn-agent'),
            'config_files': ['/etc/neutron/neutron.conf',
                             '/etc/neutron/vpn_agent.ini',
                             '/etc/neutron/l3_agent.ini',
                             '/etc/neutron/fwaas_driver.ini'],
            'log_file': '/var/log/neutron/vpn_agent.log',
        }
        service_name = 'nova-compute'
        nova_user = 'nova'
        start_dir = '/var/lib/nova'
        nova_conf = '/etc/nova/nova.conf'
        nova_api_metadata_context = {
            'service_description': 'Nova Metadata API server',
            'service_name': service_name,
            'user_name': nova_user,
            'start_dir': start_dir,
            'process_name': 'nova-api-metadata',
            'executable_name': os.path.join(bin_dir, 'nova-api-metadata'),
            'config_files': [nova_conf],
        }

        templates_dir = 'hooks/charmhelpers/contrib/openstack/templates'
        templates_dir = os.path.join(charm_dir(), templates_dir)
        render('git/upstart/neutron-agent.upstart',
               '/etc/init/neutron-dhcp-agent.conf',
               neutron_dhcp_agent_context, perms=0o644)
        render('git/upstart/neutron-agent.upstart',
               '/etc/init/neutron-l3-agent.conf',
               neutron_l3_agent_context, perms=0o644)
        render('git.upstart',
               '/etc/init/neutron-lbaas-agent.conf',
               neutron_lbaas_agent_context, perms=0o644,
               templates_dir=templates_dir)
        render('git.upstart',
               '/etc/init/neutron-metadata-agent.conf',
               neutron_metadata_agent_context, perms=0o644,
               templates_dir=templates_dir)
        render('git.upstart',
               '/etc/init/neutron-metering-agent.conf',
               neutron_metering_agent_context, perms=0o644,
               templates_dir=templates_dir)
        render('git.upstart',
               '/etc/init/neutron-ovs-cleanup.conf',
               neutron_ovs_cleanup_context, perms=0o644,
               templates_dir=templates_dir)
        render('git.upstart',
               '/etc/init/neutron-plugin-bigswitch-agent.conf',
               neutron_plugin_bigswitch_context, perms=0o644,
               templates_dir=templates_dir)
        render('git.upstart',
               '/etc/init/neutron-plugin-ibm-agent.conf',
               neutron_plugin_ibm_context, perms=0o644,
               templates_dir=templates_dir)
        render('git.upstart',
               '/etc/init/neutron-plugin-linuxbridge-agent.conf',
               neutron_plugin_linuxbridge_context, perms=0o644,
               templates_dir=templates_dir)
        render('git.upstart',
               '/etc/init/neutron-plugin-mlnx-agent.conf',
               neutron_plugin_mlnx_context, perms=0o644,
               templates_dir=templates_dir)
        render('git.upstart',
               '/etc/init/neutron-plugin-nec-agent.conf',
               neutron_plugin_nec_context, perms=0o644,
               templates_dir=templates_dir)
        render('git.upstart',
               '/etc/init/neutron-plugin-oneconvergence-agent.conf',
               neutron_plugin_oneconvergence_context, perms=0o644,
               templates_dir=templates_dir)
        render('git.upstart',
               '/etc/init/neutron-plugin-openflow-agent.conf',
               neutron_plugin_openflow_context, perms=0o644,
               templates_dir=templates_dir)
        if os_release('neutron-common') < 'mitaka':
            render('git.upstart',
                   '/etc/init/neutron-plugin-openvswitch-agent.conf',
                   neutron_plugin_openvswitch_context, perms=0o644,
                   templates_dir=templates_dir)
        else:
            render('git.upstart',
                   '/etc/init/neutron-openvswitch-agent.conf',
                   neutron_plugin_openvswitch_context, perms=0o644,
                   templates_dir=templates_dir)
        render('git.upstart',
               '/etc/init/neutron-plugin-ryu-agent.conf',
               neutron_plugin_ryu_context, perms=0o644,
               templates_dir=templates_dir)
        render('git.upstart',
               '/etc/init/neutron-plugin-sriov-agent.conf',
               neutron_plugin_sriov_context, perms=0o644,
               templates_dir=templates_dir)
        render('git/upstart/neutron-server.upstart',
               '/etc/init/neutron-server.conf',
               neutron_api_context, perms=0o644)
        render('git/upstart/neutron-agent.upstart',
               '/etc/init/neutron-vpn-agent.conf',
               neutron_vpn_agent_context, perms=0o644)
        render('git.upstart',
               '/etc/init/nova-api-metadata.conf',
               nova_api_metadata_context, perms=0o644,
               templates_dir=templates_dir)


def get_optional_interfaces():
    """Return the optional interfaces that should be checked if the relavent
    relations have appeared.
    :returns: {general_interface: [specific_int1, specific_int2, ...], ...}
    """
    optional_interfaces = {}
    if relation_ids('ha'):
        optional_interfaces['ha'] = ['cluster']
    return optional_interfaces


def check_optional_relations(configs):
    """Check that if we have a relation_id for high availability that we can
    get the hacluster config.  If we can't then we are blocked.

    This function is called from assess_status/set_os_workload_status as the
    charm_func and needs to return either "unknown", "" if there is no problem
    or the status, message if there is a problem.

    :param configs: an OSConfigRender() instance.
    :return 2-tuple: (string, string) = (status, message)
    """
    if relation_ids('ha'):
        try:
            get_hacluster_config()
        except:
            return ('blocked',
                    'hacluster missing configuration: '
                    'vip, vip_iface, vip_cidr')

    # return 'unknown' as the lowest priority to not clobber an existing
    # status.
    return 'unknown', ''


def assess_status(configs):
    """Assess status of current unit
    Decides what the state of the unit should be based on the current
    configuration.
    SIDE EFFECT: calls set_os_workload_status(...) which sets the workload
    status of the unit.
    Also calls status_set(...) directly if paused state isn't complete.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    assess_status_func(configs)()
    os_application_version_set(VERSION_PACKAGE)


def assess_status_func(configs):
    """Helper function to create the function that will assess_status() for
    the unit.
    Uses charmhelpers.contrib.openstack.utils.make_assess_status_func() to
    create the appropriate status function and then returns it.
    Used directly by assess_status() and also for pausing and resuming
    the unit.

    NOTE: REQUIRED_INTERFACES is augmented with the optional interfaces
    depending on the current config before being passed to the
    make_assess_status_func() function.

    NOTE(ajkavanagh) ports are not checked due to race hazards with services
    that don't behave sychronously w.r.t their service scripts.  e.g.
    apache2.
    @param configs: a templating.OSConfigRenderer() object
    @return f() -> None : a function that assesses the unit's workload status
    """
    required_interfaces = REQUIRED_INTERFACES.copy()
    required_interfaces.update(get_optional_interfaces())
    active_services = [s for s in services() if s not in STOPPED_SERVICES]
    return make_assess_status_func(
        configs, required_interfaces,
        charm_func=check_optional_relations,
        services=active_services, ports=None)


def pause_unit_helper(configs):
    """Helper function to pause a unit, and then call assess_status(...) in
    effect, so that the status is correctly updated.
    Uses charmhelpers.contrib.openstack.utils.pause_unit() to do the work.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    _pause_resume_helper(pause_unit, configs)


def resume_unit_helper(configs):
    """Helper function to resume a unit, and then call assess_status(...) in
    effect, so that the status is correctly updated.
    Uses charmhelpers.contrib.openstack.utils.resume_unit() to do the work.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    _pause_resume_helper(resume_unit, configs)


def _pause_resume_helper(f, configs):
    """Helper function that uses the make_assess_status_func(...) from
    charmhelpers.contrib.openstack.utils to create an assess_status(...)
    function that can be used with the pause/resume of the unit
    @param f: the function to be used with the assess_status(...) function
    @returns None - this function is executed for its side-effect
    """
    active_services = [s for s in services() if s not in STOPPED_SERVICES]
    # TODO(ajkavanagh) - ports= has been left off because of the race hazard
    # that exists due to service_start()
    f(assess_status_func(configs),
      services=active_services,
      ports=None)


def configure_apparmor():
    '''Configure all apparmor profiles for the local unit'''
    for profile in APPARMOR_PROFILES:
        context.AppArmorContext(profile).setup_aa_profile()
