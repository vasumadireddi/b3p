#! /usr/bin/env python3

import os
from copy import deepcopy as dc
from b3p import mesh_from_loft
from b3p import webs
from ruamel import yaml
import argparse
import numpy as np


def build_blade_structure(config):
    """build a rectangle blade mesh with webs, and save it to a vtp file

    Arguments:
        configfile {str} -- path to the yaml config file"""
    # config = yaml.load(open(configfile, "r"), Loader=yaml.CLoader)
    wdp = os.path.join(config["general"]["workdir"], config["general"]["prefix"])
    pckfile = f"{wdp}.pck"
    base_vtp = f"{wdp}_base.vtp"
    web_vtp = f"{wdp}_web.vtp"

    radii = eval(str(config["mesh"]["radii"]))
    config_webs = config["mesh"]["webs"]
    panel_mesh_scale = (
        config["mesh"]["panel_mesh_scale"]
        if "panel_mesh_scale" in config["mesh"]
        else []
    )

    # build the plain blade mesh
    mesh_from_loft.build_mesh(pckfile, radii, [], [], wdp, outfile=base_vtp)

    # compute the locations where the webs intersect with the blade mesh
    web_shell_intersections = webs.build_webs(base_vtp, config_webs, prefix=wdp)

    # rejoggle the datums format from the yaml file into dict
    if "coordinates" in config["mesh"]:
        ad = config["mesh"]["coordinates"]
        added_datums = dict(
            [(i, [ad[i]["base"]] + list(zip(*ad[i]["points"]))) for i in ad]
        )

    # remesh the blade, but now making sure there are nodes at the web intersections
    mesh_from_loft.build_mesh(
        pckfile,
        radii,
        config_webs,
        web_shell_intersections,
        wdp,
        config["mesh"]["n_web_points"],
        config["mesh"]["n_chordwise_points"],
        outfile=web_vtp,
        added_datums=added_datums,
        panel_mesh_scale=panel_mesh_scale,
    )


def main():
    parser = argparse.ArgumentParser(
        "Build a rectangle blade mesh with shared nodes where the webs link up with the shell"
    )
    parser.add_argument("yaml", help="config file defining the blade")
    args = parser.parse_args()

    bladeconfig = yaml.round_trip_load(open(args.yaml, "r"))
    build_blade_structure(bladeconfig)


if __name__ == "__main__":
    main()
