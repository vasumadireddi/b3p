#! /usr/bin/env python3

import argparse
import numpy as np
from ruamel import yaml
import os
from itertools import chain, zip_longest
import pickle
import json
import shutil
from numpy import array


def plyify(r, t, ply_thickness, reverse=False):
    """Fill thickness distribution using plies.

    params:
        r (list): radius distribution
        t (list): thickness distribution
        ply_thickness (float): thickness of the plies
        reverse (bool): flag determining whether the plies are numbered from the
            top or bottom of the stack
            returns:    list of plies in the form [rmin, rmax]"""
    active = []
    done = []
    np = len(r)
    for i in range(np):
        while t[i] > len(active) * ply_thickness:
            active.append([r[i], -1])
        while t[i] <= (len(active) - 1) * ply_thickness:
            pop = active.pop()
            pop[-1] = r[i]
            done.append(pop)
    for j in active:
        j[-1] = r[i]
        done.append(j)
    for i in done:
        i[0] = round(i[0] * 1e3, -1) * 1e-3
        i[1] = round(i[1] * 1e3, -1) * 1e-3
    # reverse the stack so that longest plies get the lowest ply numbers
    return done if reverse else list(reversed(done))


def ply_stack(r, t, t_ply=1.0, reverse=False, subdivisions=5000, material=11):
    """
    Taking in a r,t thickness over radius distribution, split it up in
    plies, note that it rounds down in thickness (if the last ply does not fit
    in the thickness it does not go on), so if you have thick plies,
    make sure that the mm thickness is slightly up from a whole number of plies.
    """
    x = np.linspace(min(r), max(r), subdivisions)
    y = np.interp(x, list(r), t)
    st = plyify(x, y, t_ply, reverse)
    return [[material, t_ply] + i for i in st]


def coreblock(r, t, subdivisions=200, material=11):
    """Make a thickness distribution into a block divisions."""
    assert len(r) == len(t)
    lr = len(r)
    x = np.array(sorted(list(np.linspace(min(r), max(r), int(subdivisions))) + list(r)))
    y = np.interp(0.5 * (x[:-1] + x[1:]), list(r), t)

    stack = []
    for i in range(lr - 1):
        rmin, rmax = r[i], r[i + 1]
        tmin, tmax = t[i], t[i + 1]
        tt = tmin if tmin == tmax else 0.5 * (tmin + tmax)
        if tt > 0:
            stack.append([material, tt, rmin, rmax])

    return stack


def number_stack(stack, splitstack, key, increment):
    """Create ply numbering for the plies in the stack, depends on the start key
    and increment as well as the above/below ratio (splitstack)."""
    sp = np.nan_to_num(splitstack.astype(float))
    if sp.sum() != 1.0:
        exit(f"sum {splitstack} not 1.")

    ky = np.nan_to_num(key.astype(float)).astype(int)
    inc = np.nan_to_num(increment.astype(float), nan=1).astype(int)

    ss = (len(stack) * sp).round().astype(int)
    st, sb = np.array(range(ss[0])), np.array(range(ss[1]))
    stt = ky[0] + st * inc[0]
    sbt = ky[1] + sb * inc[1]
    return [i for i in chain.from_iterable(zip_longest(stt, sbt)) if i is not None]


def get_coverage(slab, datums, rr):
    """Get the coverage of the slab and insert the datums."""
    assert "cover" in slab
    cov = slab["cover"]
    if type(cov) != str:
        return cov
    for i in datums:
        if cov.find(i) != -1:
            xy = np.array(datums[i]["xy"])
            dst = np.interp(
                rr, xy[:, 0] / datums[i]["scalex"], xy[:, 1] * datums[i]["scaley"]
            )
            cov = cov.replace(i, f"np.array({dst.tolist()})")

    return dict([(i[0], i[1:]) for i in eval(cov)])


def add_bondline_material(matdb, material_map):
    """add a bondline material to the material map, if it is not already there,
    this is needed because the 2d mesher adds elements with -1 material ID to connect the webs to the shell

    params:
        matdb (dict): material database
        material_map (dict): material map
        returns:
            material_map (dict): material map with bondline material added"""
    possible_glue_names = ["-1", "adhesive"]
    for i in matdb:
        for j in possible_glue_names:
            if matdb[i]["name"] == j:
                material_map[j] = -1

    return material_map


def export_matdb(blade, material_map):
    if "materials" in blade:
        if type(blade["materials"]) == str:
            mdb = blade["materials"]
            assert os.path.isfile(mdb)
            material_map["matdb"] = mdb
            # copy the material db over to the working directory, this means that rerunning
            # a model in a workdir is not affected by changes in the source matdb, this is
            # the desired behaviour
            shutil.copyfile(
                blade["materials"], os.path.join(blade["general"]["workdir"], mdb)
            )
        else:
            mdbname = "__matdb.yml"
            material_map["matdb"] = mdbname

            yaml.YAML(typ="unsafe", pure=True).dump(
                blade["materials"],
                open(os.path.join(blade["general"]["workdir"], mdbname), "w"),
            )
    else:
        print("no material db defined in blade file")

    matmap = os.path.join(blade["general"]["workdir"], "material_map.json")
    if type(blade["materials"]) == str:
        blade["materials"] = yaml.round_trip_load(open(blade["materials"]))
    json.dump(
        add_bondline_material(blade["materials"], material_map), open(matmap, "w")
    )
    print(f"written material map to {matmap}")


def lamplan2plies(blade):
    root_radius = blade["planform"]["z"][0][1]

    tip_radius = blade["planform"]["z"][-1][1]

    slabs = blade["laminates"]["slabs"]

    datums = blade["laminates"]["datums"] if "datums" in blade["laminates"] else {}

    allstacks = []

    # use a multiple of lamplan length as radius grid to interpolate geometric variables to
    n_s = round(tip_radius * 4)

    radius = np.linspace(0, tip_radius - root_radius, n_s)
    radius_relative = np.linspace(0, 1, n_s)
    material_map = {}

    for i in slabs:
        material = slabs[i]["material"]
        if material not in material_map:
            material_map[material] = len(material_map) + 1

        grid = slabs[i]["grid"]

        cover = get_coverage(slabs[i], datums, radius_relative)

        draping = "plies" if "draping" not in slabs[i] else slabs[i]["draping"]

        ply_thickness = float(
            1.0 if "ply_thickness" not in slabs[i] else slabs[i]["ply_thickness"]
        )

        r, t = np.array(slabs[i]["slab"]).T

        r *= (
            tip_radius - root_radius if "rscale" not in slabs[i] else slabs[i]["rscale"]
        )
        t *= ply_thickness

        if draping == "blocks":
            stack = coreblock(r, t, material=material_map[material])
        elif draping == "plies":
            stack = ply_stack(r, t, ply_thickness, material=material_map[material])

        # assigns keys to the plies in the stack depending on how the stack is split up
        # what increment the ply keys are stacked with (non-1 increment allowing interleaving of plies)
        stack_numbering = number_stack(
            stack,
            (
                np.array([1, 0])
                if "splitstack" not in slabs[i]
                else np.array(slabs[i]["splitstack"])
            ),
            (
                np.array([0, 4000])
                if "key" not in slabs[i]
                else np.array(slabs[i]["key"])
            ),
            (
                np.array([1, -1])
                if "increment" not in slabs[i]
                else np.array(slabs[i]["increment"])
            ),
        )
        allstacks.append(
            {
                "name": i.strip(),
                "grid": grid.strip(),
                "cover": cover,
                "numbering": stack_numbering,
                "stack": stack,
                "r": radius,
            }
        )
    export_matdb(blade, material_map)
    return allstacks


def main():
    parser = argparse.ArgumentParser(
        description="Split up the slab based laminate plan into plies (for laminae) and blocks (for core materials), write to a .pck file for use in draping"
    )
    parser.add_argument("yaml", help="Laminate plan yaml file")
    parser.add_argument("--out", default="__lamplan.pck", help="Output file name")
    args = parser.parse_args()

    blade = yaml.load(open(args.yaml, "r"), Loader=yaml.CLoader)

    allstacks = lamplan2plies(blade)

    of = args.out
    pickle.dump(allstacks, open(of, "wb"))

    print(f"written plydrape to {of}")


if __name__ == "__main__":
    main()
