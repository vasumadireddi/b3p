#! /usr/bin/env python3

import pyvista as pv
import numpy as np
import vtk
import fire
import time
import json
import os
import yaml
import pandas as pd


def make_shell_section(elem_id, plyarray, merge_adjacent_plies=True, zero_angle=True):
    plies = []
    # if material in next ply is the same, add it to the previous ply,
    # NOTE this does not take ply angle into account so it only works
    # for multidirectional fibre mats at angle==0 for now
    for j in plyarray[np.where(plyarray[:, 1] > 1e-6)]:
        if plies and plies[-1][1] == j[0] and merge_adjacent_plies:
            plies[-1][0] += j[1] * 1e-3
        else:
            plies.append([j[1] * 1e-3, j[0]])

    if zero_angle:
        return "".join("%f,,m%i,0\n" % tuple(i) for i in plies)
    else:
        return "".join("%f,,m%i,or%i\n" % tuple(i + [elem_id + 1]) for i in plies)


def material_db_to_ccx(materials, matmap=None, force_iso=False):
    """find the material db and write properties to a ccx block"""
    # 2 files are relevant, a material map that maps the material ID (integer)
    # in the VTK file to a key (string) in the material database and the material
    # database itself
    mat_db = None
    if os.path.isfile(matmap):  # check if the material map file is there
        gdir = os.path.dirname(matmap)
        mm = json.load(open(matmap, "r"))
        if "matdb" in mm:  # check if the material map file points to a material db
            mat_db = yaml.load(
                open(os.path.join(gdir, mm["matdb"])), Loader=yaml.CLoader
            )
        else:
            exit(
                "material map available, but no link to material db, need matdb definition to do FEA"
            )
    else:
        exit("no material map defined")

    mm_inv = {v: k for k, v in mm.items()}

    matblock = ""
    for i in materials:
        if i > 1e-6:
            material_properties = mat_db[mm_inv[int(i)]]

            # print(material_properties)
            matblock += f'** material: {material_properties["name"]}\n'
            # matblock += f"** {str(material_properties)}\n"

            if (
                "vf" in material_properties
                or "C" in material_properties
                and not force_iso
            ):
                print(material_properties["name"], "is assumed to be orthotropic")
                C = np.array(material_properties["C"])
                # https://github.com/rsmith-nl/lamprop/blob/410ebfef2e14d7cc2988489ca2c31103056da38f/lp/text.py#L96
                # https://web.mit.edu/calculix_v2.7/CalculiX/ccx_2.7/doc/ccx/node193.html
                matblock += "** orthotropic material\n"
                matblock += "*material,name=m%i\n*elastic,type=ortho\n" % i
                D = C
                D[0, 3] = C[0, 5]
                D[0, 5] = C[0, 3]
                D[1, 3] = C[1, 5]
                D[1, 5] = C[1, 3]
                D[2, 3] = C[2, 5]
                D[2, 5] = C[2, 3]
                D[3, 3] = C[5, 5]
                D[5, 5] = C[3, 3]
                D *= 1e6
                matblock += (
                    f"{D[0,0]:.4g},{D[0,1]:.4g},{D[1,1]:.4g},"
                    + f"{D[0,2]:.4g},{D[1,2]:.4g},{D[2,2]:.4g},"
                    + f"{D[3,3]:.4g},{D[4,4]:.4g},\n"
                    + f"{D[5,5]:.4g},293\n"
                )
            elif "e11" in material_properties and not force_iso:
                print(material_properties["name"], "has engineering constants")

                matblock += "** orthotropic material\n"
                matblock += (
                    "*material,name=m%i\n*elastic,type=engineering constants\n" % i
                )
                matblock += (
                    f"{material_properties['e11']:.4g},{material_properties['e22']:.4g},{material_properties['e33']:.4g},"
                    + f"{material_properties['nu12']:.4g},{material_properties['nu31']:.4g},{material_properties['nu23']:.4g},"
                    + f"{material_properties['g12']:.4g},{material_properties['g31']:.4g},\n"
                    + f"{material_properties['g23']:.4g},293\n"
                )

            else:
                print(material_properties["name"], "is assumed to be isotropic")
                print(material_properties)
                nu = min(
                    0.45,
                    max(
                        0.1,
                        float(material_properties["nu"])
                        if "nu" in material_properties
                        else material_properties["nu12"],
                    ),
                )
                E = 1e6 * float(
                    material_properties["tEx"]
                    if "tEx" in material_properties
                    else material_properties["E"]
                )
                matblock += "** isotropic material\n"
                matblock += "*material,name=m%i\n*elastic,type=iso\n" % i
                matblock += f"{E:.4g},{nu:.4g},293\n"

    return matblock


def format_eset(name, eids):
    out = f"*elset,elset={name}\n"
    for i in range(len(eids)):
        out += f"{eids[i]}"
        out += "\n" if (i % 16 == 15) else ","
    if out[-1] == ",":
        out = out[:-1] + "\n"
    return out


def compute_ply_groups(grid, prefix):
    gr = ""
    out = []
    n = 1
    for i in grid.cell_data:
        if i.startswith(prefix):
            thickness = grid.cell_data[i][:, 1].max()
            material = grid.cell_data[i][:, 0].max()
            out.append(
                {
                    "Ply Name": i,
                    "Ply ID": n,
                    "Card Image": "PLY",
                    "Mat Name": f"m{int(material)}",
                    "Thickness": thickness,
                    "Orientation": 0,
                    "Output Results": "yes",
                    "TMANU": "",
                    "DRAPE_ID": 0,
                    "ESID": n,
                }
            )
            eids = np.where(grid.cell_data[i][:, 1] > 0)[0] + 1
            gr += format_eset(i, eids)
            n += 1

    return gr, pd.DataFrame(out)


def compute_slab_groups(grid, prefix):
    gr = ""
    for i in grid.cell_data:
        if i.startswith(prefix):
            eids = np.where(grid.cell_data[i] > 0)[0] + 1
            gr += format_eset(i, eids)
    return gr


def nodebuffer(grid):
    buf = "*node,nset=nall\n"
    for n, i in enumerate(grid.points):
        buf += "%i,%f,%f,%f\n" % tuple([n + 1] + list(i))
    return buf


def element_buffer_quadratic(grid):
    conn = grid.cell_connectivity.reshape(
        (
            grid.GetNumberOfCells(),
            int(grid.cell_connectivity.shape[0] / grid.GetNumberOfCells()),
        )
    )
    buf = ""
    for n, i in enumerate(conn):
        buf += "*element,type=s8r,elset=e%i\n" % (n + 1)
        buf += "%i,%i,%i,%i,%i,%i,%i,%i,%i\n" % tuple([n + 1] + list(i + 1))

    return buf


def element_buffer_linear(grid):
    conn = grid.cell_connectivity.reshape(
        (
            grid.GetNumberOfCells(),
            int(grid.cell_connectivity.shape[0] / grid.GetNumberOfCells()),
        )
    )
    buf = ""
    for n, i in enumerate(conn):
        buf += "*element,type=s4,elset=e%i\n" % (n + 1)
        buf += "%i,%i,%i,%i,%i\n" % tuple([n + 1] + list(i + 1))

    return buf


def orientation_buffer(grid, add_centers=False):
    buf = ""
    # write orientation TODO match with element orientation, for now just align with z-axis
    for n in range(grid.GetNumberOfCells()):
        xdir, ydir = grid.cell_data["x_dir"][n], grid.cell_data["y_dir"][n]
        center = grid.cell_data["centers"][n]
        buf += "*orientation,name=or%i,system=rectangular\n" % (n + 1)
        if add_centers:
            buf += (
                ",".join(
                    [
                        format(k, ".4g")
                        for k in (xdir + center).tolist()
                        + (ydir + center).tolist()
                        + center.tolist()
                    ]
                )
                + "\n3,0\n"
            )
        else:
            buf += (
                ",".join([format(k, ".4g") for k in xdir.tolist() + ydir.tolist()])
                + "\n"
            )
    return buf


def get_loadcases(mesh, multiplier=1.0):
    loadcases = {}
    for i in mesh.point_data:
        if i.startswith("lc_"):
            print(f"loadcase {i}")
            # forces are interpolated to midside nodes, causing the sum of forces to be off,
            # compute a multiplier from the sum of the forces in the linear model here
            multiplier = 1.0  # TODO fix for quadratic meshes # mesh.point_data[i].sum() / mesh.point_data[i].sum()
            lbuf = f"** {i}\n*step\n*static\n*cload\n"
            ld = mesh.point_data[i] * multiplier
            for n, j in enumerate(ld):
                if j[0] ** 2 > 1e-8:
                    lbuf += "%i,1,%f\n" % (n + 1, j[0])
                if j[1] ** 2 > 1e-8:
                    lbuf += "%i,2,%f\n" % (n + 1, j[1])

            lbuf += "*node file,output=3d\nU,RF\n*EL FILE\nS,E\n*node print,nset=nall\nrf\n*end step\n"

            loadcases[i] = lbuf

    return loadcases


def root_clamp(mesh):
    root = np.where(mesh.points[:, 2] == mesh.points[:, 2].min())
    lbuf = "*boundary,op=new\n"
    for j in root[0]:
        lbuf += "%i,1,3\n" % (j + 1)
    return lbuf


def mesh2ccx(
    grid,
    out="test.inp",
    matmap="temp/material_map.json",
    merge_adjacent_layers=False,
    zeroangle=False,
    single_step=False,
    quadratic=False,
    add_centers=False,
    force_isotropic=False,
    export_hyperworks=False,
):
    """
    Export a grid to ccx input file

    :param  grid (_type_): Grid file (vtu)
    :param out (str, optional): Output file. Defaults to "test.inp".
    :param matmap (str, optional): material map file. Defaults to "temp/material_map.json".
    :param merge_adjacent_layers (bool, optional): _description_. Defaults to False.
    :param zeroangle (bool, optional): _description_. Defaults to False.
    :param single_step (bool, optional): _description_. Defaults to False.
    :param quadratic (bool, optional): _description_. Defaults to False.
    :param add_centers (bool, optional): _description_. Defaults to False.
    :param force_isotropic (bool, optional): _description_. Defaults to False.
    :return: _description_
    """
    gr = pv.read(grid).threshold(value=(1e-6, 1e9), scalars="thickness")
    gr.cell_data["centers"] = gr.cell_centers().points

    if quadratic:
        lf = vtk.vtkLinearToQuadraticCellsFilter()
        lf.SetInputData(gr)
        lf.Update()
        quad = lf.GetOutput()
        mesh = pv.UnstructuredGrid(quad)
    else:
        mesh = pv.UnstructuredGrid(gr)

    # export the nodes
    buf = "*node,nset=nall\n"
    for n, i in enumerate(mesh.points):
        buf += "%i,%f,%f,%f\n" % tuple([n + 1] + list(i))

    buf = nodebuffer(mesh)

    if quadratic:
        buf += element_buffer_quadratic(mesh)
    else:
        buf += element_buffer_linear(mesh)

    buf += "*elset,elset=Eall,GENERATE\n%i,%i\n" % (1, mesh.GetNumberOfCells())

    plygroups, df = compute_ply_groups(mesh, "ply_")

    buf += plygroups

    buf += compute_slab_groups(mesh, "slab_thickness_")

    plykeys = [i for i in mesh.cell_data if i.startswith("ply_")]

    plydat = np.stack(mesh.cell_data[i] for i in plykeys)
    # get all materials of all plies
    materials = np.unique(plydat[:, :, 0])

    buf += orientation_buffer(mesh, add_centers)

    matblock = material_db_to_ccx(materials, matmap=matmap, force_iso=force_isotropic)

    buf += matblock

    tic = time.perf_counter()

    blx = [
        make_shell_section(i, plydat[:, i, :], merge_adjacent_layers, zeroangle)
        for i in range(plydat.shape[1])
    ]

    toc = time.perf_counter()
    print("** time spent creating shell sections %f" % (toc - tic))

    comps = "".join(
        f"*shell section,composite,elset=e{n+1},offset=-.5"
        + (f",orientation=or{n+1}\n" if zeroangle else "\n")
        + i
        for n, i in enumerate(blx)
    )
    buf += comps
    buf += root_clamp(mesh)
    loadcases = get_loadcases(mesh)

    # write a full ccx file for each loadcase, assuming parallel execution
    if single_step:
        output = buf + "".join(loadcases.values())
        open(out, "w").write(output)
        print(f"** written ccx input file with all loadcases to {out}")
        otb = out.replace(".inp", ".csv")
    else:
        for i in loadcases:
            output = buf + loadcases[i]
            of = out.replace(".inp", f"_{i}.inp")
            open(of, "w").write(output)
            print(f"** written ccx input file to {of}")

    if export_hyperworks:
        df.to_csv(otb, index=False)
        print(f"** written plybook to hyperworks table {otb}")


def main():
    fire.Fire(mesh2ccx)
