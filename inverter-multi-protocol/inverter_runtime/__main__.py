import argparse
from .service import main

parser = argparse.ArgumentParser()
parser.add_argument('--options', default='/data/options.json')
parser.add_argument('--profiles', default='/config/inverter-profiles')
args = parser.parse_args()
main(args.options, args.profiles)
