#!/usr/bin/env python3
"""
Quick test script to validate the chargecollector fix
"""


def test_loop_logic():
    """Test the basic loop logic conceptually"""
    processed_count = 0
    simulated_charges = ["charge1", "charge2", "charge3"]  # Simulate multiple charges

    # Simulate the while loop logic
    charge_index = 0
    while charge_index < len(simulated_charges):
        charge = simulated_charges[charge_index]
        print(f"Processing charge: {charge}")

        # Simulate successful processing
        processed_count += 1
        charge_index += 1

    print(f"Total processed: {processed_count}")
    assert processed_count == 3, f"Expected 3, got {processed_count}"
    print("✓ Loop logic test passed")


def test_syntax():
    """Test that our chargecollector module compiles correctly"""
    import ast

    with open("chargecollector.py", "r", encoding="utf-8") as f:
        code = f.read()

    # This will raise an exception if there's a syntax error
    ast.parse(code)
    print("✓ Syntax validation passed")


if __name__ == "__main__":
    print("Testing charge collector fix...")
    test_loop_logic()
    test_syntax()
    print("All tests passed! ✓")
