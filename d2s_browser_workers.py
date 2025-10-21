# -*- coding: utf-8 -*-
"""
/***************************************************************************
 D2SBrowser Workers
                                 A QGIS plugin
 Worker classes for threaded API calls to D2S instance.
                              -------------------
        begin                : 2024-06-10
        git sha              : $Format:%H$
        copyright            : (C) 2024 by Geospatial Data Science Lab
        email                : jinha@purdue.edu
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
from qgis.PyQt.QtCore import QObject, pyqtSignal


class ProjectsWorker(QObject):
    """Worker to fetch projects in a background thread."""
    finished = pyqtSignal(list)  # Emits list of projects
    error = pyqtSignal(str)  # Emits error message

    def __init__(self, workspace):
        super().__init__()
        self.workspace = workspace

    def run(self):
        """Fetch projects from workspace."""
        try:
            projects = self.workspace.get_projects(has_raster=True)
            # Convert ProjectCollection to list
            self.finished.emit(projects.collection)
        except Exception as e:
            self.error.emit(str(e))


class FlightsWorker(QObject):
    """Worker to fetch flights in a background thread."""
    finished = pyqtSignal(list)  # Emits list of flights
    error = pyqtSignal(str)  # Emits error message

    def __init__(self, project):
        super().__init__()
        self.project = project

    def run(self):
        """Fetch flights from project."""
        try:
            flights = self.project.get_flights(has_raster=True)
            # Convert FlightCollection to list
            self.finished.emit(flights.collection)
        except Exception as e:
            self.error.emit(str(e))


class DataProductsWorker(QObject):
    """Worker to fetch data products in a background thread."""
    finished = pyqtSignal(list)  # Emits list of data products
    error = pyqtSignal(str)  # Emits error message

    def __init__(self, flight):
        super().__init__()
        self.flight = flight

    def run(self):
        """Fetch data products from flight."""
        try:
            data_products = self.flight.get_data_products()
            # Convert DataProductCollection to list
            self.finished.emit(data_products.collection)
        except Exception as e:
            self.error.emit(str(e))
