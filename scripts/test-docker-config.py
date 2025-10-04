#!/usr/bin/env python3
"""
Test script to validate Docker registry proxy configuration
"""

import json
import sys
from pathlib import Path

EXPECTED_REGISTRY_PROXY = "dockerproxy.lan:80"
EXPECTED_REGISTRY_MIRROR = f"http://{EXPECTED_REGISTRY_PROXY}"
EXPECTED_LOCAL_REGISTRY = "local-registry.default.svc.cluster.local:5000"


def test_docker_daemon_config():
    """Test that the Docker daemon configuration is valid and contains expected settings"""
    config_path = Path(".github/docker-daemon.json")
    
    if not config_path.exists():
        print(f"❌ Config file not found: {config_path}")
        return False
    
    try:
        with open(config_path) as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in {config_path}: {e}")
        return False
    
    # Check registry mirrors
    registry_mirrors = config.get("registry-mirrors", [])
    if EXPECTED_REGISTRY_MIRROR not in registry_mirrors:
        print(f"❌ Registry mirror not found: {EXPECTED_REGISTRY_MIRROR}")
        print(f"   Found: {registry_mirrors}")
        return False
    
    # Check insecure registries
    insecure_registries = config.get("insecure-registries", [])
    if EXPECTED_REGISTRY_PROXY not in insecure_registries:
        print(f"❌ Insecure registry not found: {EXPECTED_REGISTRY_PROXY}")
        print(f"   Found: {insecure_registries}")
        return False
    
    if EXPECTED_LOCAL_REGISTRY not in insecure_registries:
        print(f"❌ Local registry not found in insecure registries: {EXPECTED_LOCAL_REGISTRY}")
        print(f"   Found: {insecure_registries}")
        return False
    
    print("✅ Docker daemon configuration is valid")
    print(f"   Registry mirror: {EXPECTED_REGISTRY_MIRROR}")
    print(f"   Insecure registries: {EXPECTED_REGISTRY_PROXY}, {EXPECTED_LOCAL_REGISTRY}")
    return True


if __name__ == "__main__":
    success = test_docker_daemon_config()
    sys.exit(0 if success else 1)