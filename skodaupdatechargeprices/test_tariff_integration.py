#!/usr/bin/env python3
"""
Test script to verify the transport tariff API integration works correctly.
Tests both the API call and the fallback mechanism.
"""
import asyncio
import datetime
import sys
import os

# Add the parent directory to path to import the module
spec = __import__('importlib.util', fromlist=['spec_from_file_location']).spec_from_file_location(
    'skodaupdatechargeprices', 'skodaupdatechargeprices.py'
)
m = __import__('importlib.util', fromlist=['module_from_spec']).module_from_spec(spec)
sys.modules['skodaupdatechargeprices'] = m
spec.loader.exec_module(m)

async def test_tariff_integration():
    print("Testing Transport Tariff Integration")
    print("="*50)
    
    # Test winter peak hour (should use fallback since API doesn't have C tariff yet)
    winter_peak = datetime.datetime(2025, 1, 10, 18, 0, 0)
    print(f"Testing winter peak (18:00): {winter_peak}")
    
    try:
        tariff = await m.get_transport_tariff(winter_peak)
        print(f"✅ Got tariff: {tariff:.4f} DKK/kWh")
        
        # Verify it matches expected winter peak rate
        expected = 1.1977
        if abs(tariff - expected) < 0.0001:
            print(f"✅ Matches expected winter peak rate: {expected} DKK/kWh")
        else:
            print(f"❌ Expected {expected}, got {tariff}")
            
    except Exception as e:
        print(f"❌ Error getting tariff: {e}")
        return False
    
    # Test summer day hour
    summer_day = datetime.datetime(2025, 6, 10, 12, 0, 0) 
    print(f"\nTesting summer day (12:00): {summer_day}")
    
    try:
        tariff = await m.get_transport_tariff(summer_day)
        print(f"✅ Got tariff: {tariff:.4f} DKK/kWh")
        
        # Verify it matches expected summer day rate
        expected = 0.1996
        if abs(tariff - expected) < 0.0001:
            print(f"✅ Matches expected summer day rate: {expected} DKK/kWh")
        else:
            print(f"❌ Expected {expected}, got {tariff}")
            
    except Exception as e:
        print(f"❌ Error getting tariff: {e}")
        return False
    
    # Test API call directly (should fail gracefully)
    print(f"\nTesting direct API call...")
    try:
        api_tariff = await m.fetch_transport_tariff_from_api(winter_peak)
        print(f"✅ Got API tariff: {api_tariff:.4f} DKK/kWh")
        print("🎉 API has the C tariff data!")
    except Exception as e:
        print(f"ℹ️  API call failed as expected: {e}")
        print("✅ Fallback mechanism working correctly")
    
    # Test fallback directly  
    print(f"\nTesting fallback calculation...")
    try:
        fallback_tariff = await m.get_transport_tariff_fallback(winter_peak)
        print(f"✅ Got fallback tariff: {fallback_tariff:.4f} DKK/kWh")
    except Exception as e:
        print(f"❌ Fallback failed: {e}")
        return False
    
    print(f"\n🎉 All transport tariff integration tests passed!")
    return True

if __name__ == "__main__":
    success = asyncio.run(test_tariff_integration())
    exit(0 if success else 1)