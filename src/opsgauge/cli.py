"""OpsGauge command-line interface."""
import argparse
import importlib
import os
import sys

COMMANDS={
 'list':('data.materialize',['--list']),
 'materialize':('data.materialize',[]),
 'restore-data':('data.restore',[]),
 'score':('evaluation.score',[]),
 'verify':('verification.release',[]),
 'check-environments':('verification.environments',[]),
 'check-scoring':('verification.scoring',[]),
}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',help='OpsGauge checkout containing data/manifest.json; also OPSGAUGE_HOME')
    parser.add_argument('command',choices=COMMANDS)
    parser.add_argument('arguments',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    if args.data_root:os.environ['OPSGAUGE_HOME']=args.data_root
    name,prefix=COMMANDS[args.command]
    module=importlib.import_module('opsgauge.'+name)
    sys.argv=['opsgauge '+args.command,*prefix,*args.arguments]
    module.main()
