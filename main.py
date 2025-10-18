from datetime import datetime, timezone, timedelta
import logging
from proxmoxer import ProxmoxAPI
import PyNUTClient.PyNUT as PyNUT
import schedule
from schedule import every, repeat, run_pending
import time
import sys


UPS_NAME = ''
NUT_HOST = ''
NUT_USER = ''
NUT_PASS = ''
PROXMOX_HOST = ''
PROXMOX_USER = 'WHO@pam'
PROXMOX_PASS = ''

proxmox = ProxmoxAPI(PROXMOX_HOST, user=PROXMOX_USER, password=PROXMOX_PASS, verify_ssl=False)
logger = logging.getLogger()
handler = logging.StreamHandler(sys.stdout)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def shutdown_proxmox_node(node_name):
    logger.info(f'Shutting down {node_name}')
    proxmox.nodes(node_name).status.post(command='shutdown')
    logger.info(f'Cancelling shutdown job of {node_name}')
    return schedule.CancelJob


def get_vms_by_tag(tag):
    resources = proxmox.cluster.resources.get()
    return [ (r['name'], r['node'], r['id']) for r in resources
             if r['type'] in ['lxc', 'qemu'] and tag in r.get('tags','').split(';')]


def shutdown_vm_by_id(vmid, node):
    logger.info(f'Shutting down {vmid}')
    type, num = vmid.split('/')
    if type == 'lxc':
        logger.debug(f'ID {vmid} was determined to be LXC')
        proxmox.nodes(node).lxc(num).status.shutdown()
    if type == 'qemu':
        logger.debug(f'ID {vmid} was determined to be QEMU VM')
        proxmox.nodes(node).qemu(num).status.shutdown()
    logger.info(f'Cancelling shutdown job of {vmid}')
    return schedule.CancelJob

def start_shutdowns():
    print('Setting Shutdown Schedules')
    current_utc_time = datetime.now(timezone.utc)

    ## After 5 minutes of no power, shutdown Tier 3 apps
    for vm in get_vms_by_tag('tier.3'):
        logger.debug(f'Tier 3 VM ID {vm[2]} will be shut down in 5 minutes from {current_utc_time.isoformat()}')
        schedule.every().day.at((current_utc_time + timedelta(minutes=5)).strftime('%H:%M'), 'UTC').do(
            shutdown_vm_by_id, id=vm[2], node=vm[1]).tag('shutdown')
    ## After 10 minutes of no power, shutdown Tier 2 apps
    for vm in get_vms_by_tag('tier.2'):
        logger.debug(f'Tier 2 VM ID {vm[2]} will be shut down in 10 minutes from {current_utc_time.isoformat()}')
        schedule.every().day.at((current_utc_time + timedelta(minutes=10)).strftime('%H:%M'), 'UTC').do(
            shutdown_vm_by_id, id=vm[2], node=vm[1]).tag('shutdown')
    ## After 15 minutes of no power, shutdown Tier 1 apps
    for vm in get_vms_by_tag('tier.1'):
        logger.debug(f'Tier 1 VM ID {vm[2]} will be shut down in 15 minutes from {current_utc_time.isoformat()}')
        schedule.every().day.at((current_utc_time + timedelta(minutes=15)).strftime('%H:%M'), 'UTC').do(
            shutdown_vm_by_id, id=vm[2], node=vm[1]).tag('shutdown')
    ## After 20 minutes, shut down pve and pve03
    for node in ['pve', 'pve03']:
        logger.debug(f'Proxmox Node {node} will be shut down in 20 minutes from {current_utc_time.isoformat()}')
        schedule.every().day.at((current_utc_time + timedelta(minutes=20)).strftime('%H:%M'), 'UTC').do(
            shutdown_proxmox_node, node=node).tag('shutdown')

def check_power_status(ups):
    status = ups.GetUPSVars(UPS_NAME)[b'ups.status']
    logger.info(f'UPS Status: {status}')
    return status

def check_ups_connected(ups):
    return ups.CheckUPSAvailable(UPS_NAME)

@repeat(every().minute)
def monitor_power():
    ups = PyNUT.PyNUTClient(host=NUT_HOST, login=NUT_USER, password=NUT_PASS)
    if not check_ups_connected(ups):
        logger.error(f'UPS Not connected')
    power_status = check_power_status(ups).decode('utf-8').split(' ')
    existing_shutdown_jobs = schedule.get_jobs('shutdown')
    logger.debug(f'Existing shutdown jobs: {existing_shutdown_jobs}')

    if 'OL' in power_status:
        logger.info('Power is online and good')
        if existing_shutdown_jobs:
            logger.info('Cancelling any pending shutdown jobs since power is online! ')
            schedule.clear('shutdown')
    elif 'OB' in power_status:
        logger.info('Power is offline, we are on battery')
        if not existing_shutdown_jobs:
            logger.debug('No existing shutdown jobs, creating them now')
            start_shutdowns()
        else:
            logger.info('Shutdown schedules already running')
    elif 'LB' in power_status:
        logger.warning('Low Battery! Clearing Schedule since upsd will shut us down soon.')
        schedule.clear('shutdown')


logger.info('Monitor Start!')
while True:
    run_pending()
    time.sleep(1)