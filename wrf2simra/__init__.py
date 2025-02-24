import numpy as np
from pathlib import Path

from siso.coords import graph, Coords
from siso.reader import simra, wrf

from vtkmodules.vtkFiltersCore import vtkProbeFilter
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellLocator
from vtkmodules.vtkCommonDataModel import (
    vtkDataSet, vtkStructuredGrid, vtkCellArray,
    VTK_HEXAHEDRON
)

from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray, vtk_to_numpy
from vtkmodules.vtkIOXML import vtkXMLStructuredGridWriter

from scipy.io import FortranFile

class WRFConverter:
    def _interpolate(self, orig_data, sub_grid):
        pfilter = vtkProbeFilter()
        pfilter.SetSourceData(orig_data)
        pfilter.SetInputData(sub_grid)
        pfilter.SetCellLocatorPrototype(vtkCellLocator())
        pfilter.Update()
        return pfilter.GetStructuredGridOutput()

    def __init__(self, infile, inmesh, outfile):
        self._infile = infile
        self._inmesh = inmesh
        self._outfile = outfile

    def doConvert(self):
        print(self._inmesh)
        with simra.SIMRA3DMeshReader(Path(self._inmesh)) as m, \
             wrf.WRFReader(Path(self._infile)) as w:
            nstep = w.nsteps-1

            # Setup coordinate converter
            src = Coords.find('geodetic')
            tgt = Coords.find('utm:32u')
            converter = graph.path(src, tgt)

            # Setup VTK mesh for WRF input grid
            geom,nodes = next(wrf.WRFGeodeticGeometryField(w, 'HGT').patches(nstep))
            inputGrid = vtkStructuredGrid()
            shape = geom.topology.shape
            inputGrid.SetDimensions(*(s + 1 for s in shape)),
            points = vtkPoints()
            nodes = converter.points(src, tgt, nodes, None)
            nodes = nodes.reshape(*inputGrid.GetDimensions(), -1).transpose(2,1,0,3).reshape(-1,3)
            points.SetData(numpy_to_vtk(nodes, deep=True))
            inputGrid.SetPoints(points)

            # Setup VTK mesh for SIMRA output grid
            geom,nodes = next(simra.SIMRAGeometryField(m).patches(0))
            outputGrid = vtkStructuredGrid()
            shape = geom.topology.shape
            outputGrid.SetDimensions(*(s + 1 for s in shape))
            points = vtkPoints()
            points.SetData(numpy_to_vtk(nodes, deep=True))
            outputGrid.SetPoints(points)

            # Add fields to input grid
            target = inputGrid.GetPointData()
            wind = wrf.WRFVectorField('WIND', ['U','V','W'], w)
            wind = converter.vectors(src, tgt, next(wind.patches(nstep))[1], None)
            wind = wind.reshape(*inputGrid.GetDimensions(), -1).transpose(2,1,0,3).reshape(-1,3)
            array = numpy_to_vtk(wind, deep=True)
            array.SetName('WIND')
            target.AddArray(array)

            new_fields = self._interpolate(inputGrid, outputGrid)

            W = vtk_to_numpy(new_fields.GetPointData().GetArray('WIND'))

            nx = outputGrid.GetDimensions()[0]
            ny = outputGrid.GetDimensions()[1]
            nz = outputGrid.GetDimensions()[2]

            W2 = np.zeros(W.shape)
            for i in range(0, nx):
                for j in range(0, ny):
                    for k in range(0, nz):
                        vtkIdx = i + (j + k * ny) * nx
                        simraIdx = k + (i + j * nx) * nz
                        W2[simraIdx,:] = W[vtkIdx,:]
            z = np.zeros((W.shape[0], 8))
            W = np.hstack([W2, z])
            strat = np.float32(np.zeros((W.shape[0], 1)))

            ftype = np.dtype(f'<f4')
            dtype = np.dtype(f'<u4')
            with FortranFile(self._outfile,'w',header_dtype=dtype) as f:
                T = np.float32(np.insert(W.flatten(), 0, 100.0))
                f.write_record(T.transpose())
