import glob
import os
import pathlib
import subprocess
import requests
import time
import dotenv
import vision.client.library.configuration as pc_conf
import concurrent.futures
import web3


def check_service_nodes():
    current_dir = os.path.dirname(__file__)
    file_path = os.path.join(current_dir, "hub_abi.txt")

    abi = '[{"type":"function","name":"getServiceNodes","inputs":[],"outputs":[{"name":"","type":"address[]","internalType":"address[]"}],"stateMutability":"view"}]'
    ethereum_hub_address = os.getenv('ETHEREUM_HUB')
    provider_url = os.getenv('ETHEREUM_PROVIDER')
    
    w3 = web3.Web3(web3.HTTPProvider(provider_url))
    hub_contract = w3.eth.contract(address=ethereum_hub_address, abi=abi)

    max_tries = 100
    while True:
        print("Checking for registered service nodes")
        service_nodes = hub_contract.functions.getServiceNodes().call()
        if len(service_nodes) > 0:
            print(f"Service Nodes: {service_nodes}")
            break
        max_tries -= 1
        print(f"No service nodes found")
        if max_tries == 0:
            raise TimeoutError('Service node did not start in time')
        time.sleep(5)

def wait_for_service_node_to_be_ready():
    max_tries = 100
    while True:
        max_tries -= 1
        if max_tries == 0:
            raise TimeoutError('Service node did not start in time')
        try:
            response = requests.get('http://localhost:8081/bids?source_blockchain=0&destination_blockchain=1', timeout=60)
        except requests.exceptions.ConnectionError:
            print('Service node not ready yet')
            time.sleep(5)
            continue
        # response.raise_for_status()
        print(response.status_code)
        bids = response.json()
        if len(bids) > 0:
            print('Service node is ready')
            break
        time.sleep(5)


def teardown_environment(stack_id = ''):
    configure_nodes({}, stack_id)    

def run_command(command, cwd, env_vars):
    # Merge environment variables
    env = {**os.environ, **env_vars}

    print(f'Running command: {command} in {cwd} with environment: {env_vars}')
    process = subprocess.Popen(command, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, bufsize=0)
    for line in process.stdout:
        print(line.decode(), end='')
    process.wait()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, command)

def configure_nodes(config, stack_id):
    vision_ethereum_contracts_dir = os.getenv('VISION_ETHEREUM_CONTRACTS')
    vision_ethereum_contracts_version = os.getenv('VISION_ETHEREUM_CONTRACTS_VERSION', 'development')
    if not vision_ethereum_contracts_version or vision_ethereum_contracts_version == '':
        vision_ethereum_contracts_version = 'development'
    vision_service_node_dir = os.getenv('VISION_SERVICE_NODE')
    vision_service_node_version = os.getenv('VISION_SERVICE_NODE_VERSION', 'development')
    if not vision_service_node_version or vision_service_node_version == '':
        vision_service_node_version = 'development'
    vision_validator_node_dir = os.getenv('VISION_VALIDATOR_NODE')
    vision_validator_node_version = os.getenv('VISION_VALIDATOR_NODE_VERSION', 'development')
    if not vision_validator_node_version or vision_validator_node_version == '':
        vision_validator_node_version = 'development'

    if not vision_ethereum_contracts_dir:
        raise EnvironmentError('VISION_ETHEREUM_CONTRACTS environment variable not set')

    print(f'Configuring tests with: Ethereum Contracts {vision_ethereum_contracts_version}, Service Node {vision_service_node_version}, Validator Node {vision_validator_node_version}')

    # Teardown
    if not config:
        print('Tearing down the environment')
        # Dump all the logs
        env_vars = {'STACK_IDENTIFIER': stack_id}
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(run_command, 'make docker-logs', vision_validator_node_dir, env_vars),
                executor.submit(run_command, 'make docker-logs', vision_service_node_dir, env_vars),
                executor.submit(run_command, 'make docker-logs', vision_ethereum_contracts_dir, env_vars)
            ]
            concurrent.futures.wait(futures)

        # Remove the containers
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(run_command, 'make docker-remove', vision_validator_node_dir, env_vars),
                executor.submit(run_command, 'make docker-remove', vision_service_node_dir, env_vars),
                executor.submit(run_command, 'make docker-remove', vision_ethereum_contracts_dir, env_vars)
            ]
            concurrent.futures.wait(futures)

        return

    # Configure Ethereum contracts
    if 'ethereum_contracts' in config:
        ethereum_contracts_command = 'make docker-local'
        ethereum_contracts_env_vars = {'DOCKER_TAG': vision_ethereum_contracts_version, 'STACK_IDENTIFIER': stack_id, 'ARGS': '--no-build'}
    else:
        ethereum_contracts_command = 'make docker-remove'
        ethereum_contracts_env_vars = {'STACK_IDENTIFIER': stack_id}
    run_command(ethereum_contracts_command, vision_ethereum_contracts_dir, ethereum_contracts_env_vars)

    # Configure Service Node
    if 'service_node' in config:
        instance_count = config['service_node'].get('instance_count', 1)
        service_node_command = f'make docker INSTANCE_COUNT="{instance_count}"'
        # TODO: Allow service nodes to support multiple networks?
        service_node_env_vars = {'DOCKER_TAG': vision_service_node_version, 'STACK_IDENTIFIER': stack_id, 'ETHEREUM_NETWORK': '1', 'NO_BUILD': 'true'}
    else:
        service_node_command = 'make docker-remove'
        service_node_env_vars = {'STACK_IDENTIFIER': stack_id}

    # Configure Validator Node
    if 'validator_node' in config:
        instance_count = config['validator_node'].get('instance_count', 1)
        validator_node_command = f'make docker INSTANCE_COUNT="{instance_count}"'
        validator_node_env_vars = {'DOCKER_TAG': vision_validator_node_version, 'STACK_IDENTIFIER': stack_id, 'ETHEREUM_NETWORK': '1', 'NO_BUILD': 'true'}
    else:
        validator_node_command = 'make docker-remove'
        validator_node_env_vars = {'STACK_IDENTIFIER': stack_id}

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(run_command, service_node_command, vision_service_node_dir, service_node_env_vars),
            executor.submit(run_command, validator_node_command, vision_validator_node_dir, validator_node_env_vars),
        ]
        concurrent.futures.wait(futures)


def configure_client(stack_id, instance=1):
    if not os.getenv('VISION_ETHEREUM_CONTRACTS'):
        raise EnvironmentError('VISION_ETHEREUM_CONTRACTS environment variable not set')
    contracts_dir = os.getenv('VISION_ETHEREUM_CONTRACTS')
    current_dir = os.path.dirname(os.path.realpath(__file__))

    # TODO: Return one library instance per instance from the stack id
    for file in [f'{contracts_dir}/data/*{stack_id}-{instance}/*/all.env', f'{current_dir}/../base.env']:
        resolved_path = glob.glob(file)
        if not resolved_path:
            raise FileNotFoundError(f'Environment path {file} not found')
        for env_file in resolved_path:
            if not pathlib.Path(env_file).exists():
                raise FileNotFoundError(f'Environment file {env_file} not found')
            dotenv.load_dotenv(env_file)
    
    pc_conf.load_config(None, True)
