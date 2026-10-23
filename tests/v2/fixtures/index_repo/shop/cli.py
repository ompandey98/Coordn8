"""Command line for the shop."""
import argparse

from shop.cart import Cart


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.parse_args(argv)
    cart = Cart().add_item("pen", 2.5, 4)
    print(cart.total())
    return 0


if __name__ == "__main__":
    main()
