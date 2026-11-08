"""Generate Pulse0 Pro license keys.

Usage:
    python -m enterprise.keygen           # generate one key
    python -m enterprise.keygen 5         # generate five keys
    python -m enterprise.keygen --verify PULSE0-XXXX-...   # verify a key
"""
import sys
from enterprise.license import generate_license_key, validate_license


def main():
    args = sys.argv[1:]
    if args and args[0] == "--verify":
        key = args[1] if len(args) > 1 else input("Key: ").strip()
        ok = validate_license(key)
        print(f"{'VALID' if ok else 'INVALID'}: {key}")
        sys.exit(0 if ok else 1)
    count = int(args[0]) if args else 1
    for _ in range(count):
        key = generate_license_key()
        print(key)


if __name__ == "__main__":
    main()
