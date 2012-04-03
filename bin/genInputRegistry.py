#!/usr/bin/env python

# 
# LSST Data Management System
# Copyright 2008, 2009, 2010 LSST Corporation.
# 
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the LSST License Statement and 
# the GNU General Public License along with this program.  If not, 
# see <http://www.lsstcorp.org/LegalNotices/>.
#

import glob
from optparse import OptionParser
import os
import re
import shutil
import sqlite as sqlite3
import sys
import lsst.daf.base as dafBase
import lsst.afw.image as afwImage
import lsst.skypix as skypix

def process(dirList, inputRegistry, outputRegistry="registry.sqlite3"):
    if os.path.exists(outputRegistry):
        print >>sys.stderr, "Output registry exists; will not overwrite."
        sys.exit(1)
    if inputRegistry is not None:
        if not os.path.exists(inputRegistry):
            print >>sys.stderr, "Input registry does not exist."
            sys.exit(1)
        shutil.copy(inputRegistry, outputRegistry)

    conn = sqlite3.connect(outputRegistry)

    done = {}
    if inputRegistry is None:
        # Create tables in new output registry.
        cmd = """CREATE TABLE raw (id INTEGER PRIMARY KEY AUTOINCREMENT,
            run INT, rerun INT, band TEXT, camcol INT, frame INT,
            taiObs TEXT, expTime DOUBLE)"""
        # cmd += ", unique(run, band, camcol, frame))"
        conn.execute(cmd)
        cmd = "CREATE TABLE raw_skyTile (id INTEGER, skyTile INTEGER)"
        # cmd += ", unique(id, skyTile), foreign key(id) references raw(id))"
        conn.execute(cmd)
    else:
        cmd = """SELECT run || '_R' || rerun || '_B' || band ||
            '_C' || camcol || '_F' || frame FROM raw"""
        for row in conn.execute(cmd):
            done[row[0]] = True

    qsp = skypix.createQuadSpherePixelization()

    try:
        for dir in dirList:
            if dir.endswith("runs"):
                for runDir in glob.iglob(os.path.join(dir, "*")):
                    processRun(runDir, conn, done, qsp)
            else:
                processRun(dir, conn, done, qsp)
    finally:
        print >>sys.stderr, "Cleaning up..."
        conn.close()

def processRun(runDir, conn, done, qsp):
    nProcessed = 0
    nSkipped = 0
    nUnrecognized = 0
    print >>sys.stderr, runDir, "... started"
    for fits in glob.iglob(
            os.path.join(runDir, "*", "corr", "[1-6]", "fpC*.fit.gz")):
        m = re.search(r'(\d+)/corr/([1-6])/fpC-(\d{6})-([ugriz])\2-(\d{4}).fit.gz', fits)
        if not m:
            print >>sys.stderr, "Warning: Unrecognized file:", fits
            nUnrecognized += 1
            continue

        (rerun, camcol, run, band, frame) = m.groups()
        rerun = int(rerun)
        camcol = int(camcol)
        run = int(run)
        frame = int(frame)
        key = "%d_R%d_B%s_C%d_F%d" % (run, rerun, band, camcol, frame)
        if done.has_key(key):
            nSkipped += 1
            continue

        md = afwImage.readMetadata(fits)
        expTime = md.get("EXPTIME")
        (year, month, day) = md.get("DATE-OBS").split("-")
        (hour, minute, second) = md.get("TAIHMS").split(":")
        seconds = float(second)
        second = int(seconds)
        taiObs = dafBase.DateTime(int(year), int(month), int(day), int(hour),
                int(minute), second, dafBase.DateTime.TAI)
        taiObs = dafBase.DateTime(taiObs.nsecs() +
                long((seconds - second) * 1000000000), dafBase.DateTime.TAI)
        taiObs = taiObs.toString()[:-1]
        conn.execute("""INSERT INTO raw VALUES
            (NULL, ?, ?, ?, ?, ?, ?, ?)""",
            (run, rerun, band, camcol, frame, taiObs, expTime))
   
        for row in conn.execute("SELECT last_insert_rowid()"):
            id = row[0]
            break

        wcs = afwImage.makeWcs(md)
        poly = skypix.imageToPolygon(wcs,
                md.get("NAXIS1"), md.get("NAXIS2"),
                padRad=0.000075) # about 15 arcsec
        pix = qsp.intersect(poly)
        for skyTileId in pix:
            conn.execute("INSERT INTO raw_skyTile VALUES(?, ?)",
                    (id, skyTileId))

        nProcessed += 1
        if nProcessed % 100 == 0:
            conn.commit()

    conn.commit()
    print >>sys.stderr, runDir, \
            "... %d processed, %d skipped, %d unrecognized" % \
            (nProcessed, nSkipped, nUnrecognized)

if __name__ == "__main__":
    parser = OptionParser(usage="""%prog [options] DIR ...

DIR may be either a root directory containing a 'raw' subdirectory
or a visit subdirectory.""")
    parser.add_option("-i", dest="inputRegistry", help="input registry")
    parser.add_option("-o", dest="outputRegistry", default="registry.sqlite3",
            help="output registry (default=registry.sqlite3)")
    (options, args) = parser.parse_args()
    if len(args) < 1:
        parser.error("Missing directory argument(s)")
    process(args, options.inputRegistry, options.outputRegistry)
