#!/usr/bin/env python
''' A simple GUI to view a raw waveforms of a SIS3316 ADC events (online or offline).
'''
# Author: Sergey Ryzhikov (sergey-inform@ya.ru), 2015
# License: GPLv2


#TODO: convert buttons and other controls to gui snippets

import sys,os
import argparse
import wx
import time
import io
from threading import Thread
import math #round
import numpy as np

from parse import Parse
from integrate import integrate, Features

import matplotlib	#TODO: put this inside GUI init
matplotlib.use('WXAgg')
from matplotlib.figure import Figure

from matplotlib.backends.backend_wxagg import \
	FigureCanvasWxAgg as FigCanvas, \
	NavigationToolbar2WxAgg as NavigationToolbar

from matplotlib.ticker import MultipleLocator, FormatStrFormatter #ticks

minorLocator = MultipleLocator(100)

WINDOW_TITLE = "SIS3316 ACD data waveforms viewer"
TIMER_RATE = 1900 #milliseconds
FONT_SIZE = 9

# Button definitions
ID_PAUSE = wx.NewId()

# Globals
args = None # the argparse.Namespace() object, config. options
events = [] #TODO: refactor
hist = []

# Define notification event for thread completion
EVT_DATA_READY_ID= wx.NewId()

def EVT_DATA_READY(win, func):
	"""Define Result Event."""
	win.Connect(-1, -1, EVT_DATA_READY_ID, func)


class DataReadyEvent(wx.PyEvent):
	"""Event is generated by parser when a new data arrives and GUI state is ready()."""
	def __init__(self, data):
		wx.PyEvent.__init__(self)
		self.SetEventType(EVT_DATA_READY_ID)
		self.data = data

matplotlib.rcParams.update({'font.size': FONT_SIZE})

class EventParser(Thread):
	"""Thread class that executes event processing."""
	def __init__(self, notify_window):
		Thread.__init__(self)
		self._notify_window = notify_window
		self._abort_flag = False
		self._pause_flag = False	#TODO:
		self._daq_flag = False	#TODO: write data to a file
		self.start() # start the thread on it's creation

	def run(self):
		global args, events, hist
		p = Parse(args.infile )
		data = None
		
		while True:
			if self._abort_flag:
				print("Worker aborted")
				return
				
			if self._pause_flag:
				time.sleep(0.1)
				continue
			
			try:
				data = p.next()
				
			except StopIteration:
				time.sleep(0.5)
				continue
				
			vals  = integrate(data, features = ('max'))
			e_max = vals.max
			
			if self._notify_window.ready:
				self._notify_window.ready = False
				wx.PostEvent(self._notify_window, DataReadyEvent(data))
				sys.stderr.write("progress: %2.3f%%\n" % (100.0 * p.progress()) )

			events.append(data)
			
			hist.append(e_max)
		
				
	def abort(self):
		""" Method for use by main thread to signal an abort."""
		print("Worker abort")
		self._abort_flag = True
	
	def pause(self):
		""" Method for use by main thread to pause the parser."""
		self._pause_flag = True
		
	def resume(self):
		""" Method for use by main thread to resume the parser after pause()."""
		self._pause_flag = False


class CustomNavigationToolbar(NavigationToolbar):
		""" Only display the buttons we need. """
		def __init__(self,canvas_,parent_):
			self.toolitems = (
				('Home', 'Reset original view', 'home', 'home'),
				('Pan', 'Pan axes with left mouse, zoom with right', 'move', 'pan'),
				('Zoom', 'Zoom to rectangle', 'zoom_to_rect', 'zoom'),
				(None, None, None, None),
				('Save', 'Save the figure', 'filesave', 'save_figure'),
				)
			NavigationToolbar.__init__(self,canvas_)
			
		def set_history_buttons(self): # Workaround for some bug
			pass


class BaselineCtrl(wx.SpinCtrl):
	def __init__(self, *args_, **kwargs):
		global args
		
		kwargs['initial'] = args.baseline
		kwargs['size'] = wx.Size(60, -1)
		wx.SpinCtrl.__init__(self, *args_, **kwargs)
		
		self.Bind(wx.EVT_SPINCTRL, self.OnSpin)

	def OnSpin(self,event):
		global args
		args.baseline = self.GetValue()


class PlotPanel(wx.Panel):
	def __init__(self, parent, figure):
		
		super(PlotPanel, self).__init__(parent)
		
		self.figure = figure
		
		self.sizer = wx.BoxSizer(wx.VERTICAL)
		self.canvas = FigCanvas(self, -1, self.figure)
		
		# Place all elements in the sizer
		self.sizer.Add(self.canvas, 1, flag= wx.TOP| wx.GROW)
		self.SetSizer(self.sizer)
		

class WaveformPanel(PlotPanel):
	def __init__(self, parent):
		global args
		
		dpi = 100
		
		self.figure = Figure((2.0, 2.0), dpi=dpi)
		self.axes = self.figure.add_subplot(111)
		self.axes.set_axis_bgcolor('black')
		self.axes.set_title('Signal Waveform', size=FONT_SIZE+1)
		
		self.f_autoscale = True
		
		super(WaveformPanel, self).__init__(parent, self.figure)
		
		# Controls
		self.baseline = BaselineCtrl(self)
		
		self.autoscale = wx.CheckBox(self,label="Autoscale")
		self.autoscale.SetValue(self.f_autoscale)
		self.autoscale.Bind(wx.EVT_CHECKBOX, self.OnAutoscale)
		
		csizer = wx.BoxSizer(wx.HORIZONTAL)
		toolbar = CustomNavigationToolbar(self.canvas, self)
		csizer.Add(toolbar,0)
		csizer.Add((0, 0), 1, wx.EXPAND) #spacer
		csizer.Add(wx.StaticText(self,label="Baseline:"), 0, flag=wx.ALIGN_CENTER_VERTICAL)
		csizer.Add(self.baseline, 0, flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_RIGHT)
		csizer.AddSpacer(10)
		csizer.Add(self.autoscale, 0, flag=wx.ALIGN_CENTER_VERTICAL)
		self.sizer.Add(csizer, 0, flag=wx.EXPAND)
		
		self.axes.grid(color='yellow', linestyle='dotted', alpha=0.7)
		
		
		# Fade effect
		#~ self.backlog = 20
		#~ self.graphs = [self.axes.plot([],[], '.-')[0] for i in range(0,self.backlog)]
		self.graphs = []
		
		y_limits =  self.axes.get_ylim()
		x = args.baseline
		self.vline = self.axes.plot([], [], 'y--')[0]
		
		
	def DrawWaveform(self, ax, event_data):
		""" Plot waveforms."""
		data = event_data.raw
		backlog = 8
		
		if len(self.graphs) > backlog:
			self.graphs.pop(0).remove()
		
		if data:
			ydata = [d for d in data]
			xdata = range(0,len(data))
			
			#~ self.graph.set_xdata(xdata)
			#~ self.graph.set_ydata(data)
			
			self.graphs.append( self.axes.plot(xdata,ydata, '-g')[0] )
			
			current_limits =  ax.get_ylim()
			self.vline.set_xdata( (args.baseline,) * 2) # (x,x)
			self.vline.set_ydata(current_limits)
			
			if self.f_autoscale:
				baseline_position = 0.3
				
				vals = integrate(event_data, features = ('max'))
				e_baseline = vals.bl
				
				new_limits = self.autoscale_baseline(current_limits, max(data), min(data), e_baseline, baseline_position)
				ax.set_ylim( new_limits)
				ax.set_xlim(0,len(data))
				

			#Fade effect
			for i,g in enumerate(self.graphs):
				g.set_alpha(float(i)/backlog)
		
			self.canvas.draw()
			
	
	def autoscale_baseline(self, old_limits, new_max, new_min, new_baseline, baseline_position ):
		""" Return such a new limits that the baseline would stay on the same place """
		old_min, old_max = old_limits
		
		range_high = float(new_max - new_baseline)/(1-baseline_position)
		range_low = float(new_baseline - new_min)/baseline_position
		range_old = old_max - old_min
		
		range_new = max(range_high, range_low)
		
		if range_old > range_new and new_max < old_max and new_min > old_min:
			return old_limits
			
		else:
			high = new_baseline + range_new * (1-baseline_position)
			low = new_baseline - range_new * baseline_position
			
			#round to 100
			high = math.ceil(high/100.0) * 100.0
			low = math.floor(low/100.0) * 100.0
			
			return (low, high)
	
	def OnAutoscale(self, event):
		""" Autoscale checkbox toggled """
		self.f_autoscale = self.autoscale.GetValue()

			
class HistPanel(PlotPanel):
	def __init__(self, parent):
		
		dpi = 100
		self.figure = Figure((3.0, 3.0), dpi=dpi)
		self.axes = self.figure.add_subplot(111)
		
		super(HistPanel, self).__init__(parent, self.figure)	
		
		toolbar = CustomNavigationToolbar(self.canvas, self)
		
		pauseBtn = wx.Button(self, wx.ID_ANY, "Pause")
		pauseBtn.Bind(wx.EVT_BUTTON, self.onTogglePause)
		
		clearBtn = wx.Button(self, wx.ID_ANY, "Clear")
		clearBtn.Bind(wx.EVT_BUTTON, self.onToggleClear)
		
		csizer = wx.BoxSizer(wx.HORIZONTAL)
		csizer.Add(toolbar,0)
		csizer.Add((0, 0), 1, wx.EXPAND) #spacer
		csizer.Add(pauseBtn, border=5, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL)
		csizer.Add(clearBtn, border=5, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL)
		
		self.sizer.Add(csizer,0, flag=wx.EXPAND)
		
		self.paused = False
		
	def onTogglePause(self,event):
		if self.paused:
			event.EventObject.SetLabel("Pause")
		else:
			event.EventObject.SetLabel("Continue")
		
		self.paused = not self.paused #toggle
		
	def onToggleClear(self,event):
		global hist
		hist[:] = [] #purge
		self.axes.cla()
		self.canvas.draw()
		print("Clear")
		
	def DrawHist(self):
		global hist
		print 'events:',  len(events)
		
		if self.paused:
			return
		
		self.axes.cla()
		
		#TODO: refactor
		hist = hist[-100000:]
		
		arr = np.array(hist)
		
		mean = np.mean(arr)
		std = np.std(arr)
		
		min_ = np.percentile(arr, 1)
		max_ = np.percentile(arr, 99)
		
		range_ = (min_, max_)
		
		self.axes.set_title('Peak Histogram', size=FONT_SIZE+1)
		hist1 =self.axes.hist(arr, 100, range=range_, histtype='stepfilled', facecolor='g', zorder=0)
		self.axes.set_ylim(0,max(hist1[0]))
		
		#~ self.axes.set_xlim(-1000, 1000)
		
		for tick in self.axes.get_xticklabels():
			tick.set_rotation(30)

		# Grid
		self.axes.xaxis.grid(True, zorder=2,color='k', linestyle='dotted', alpha=0.7)
		self.axes.xaxis.grid(True, which='minor', color='k', linestyle='dotted', alpha=0.3)
		# Remove ticks
		#~ for tic in self.axes.xaxis.get_major_ticks():
			#~ tic.tick1On = tic.tick2On = False
		
		self.canvas.draw_idle()
		

class MainFrame(wx.Frame):
	def __init__(self, parent, id): 
		wx.Frame.__init__(self, parent, id, WINDOW_TITLE)
		
		# Add a panel so it looks the correct on all platforms
		
		# Timer
		self.timer = wx.Timer(self)
		self.Bind(wx.EVT_TIMER, self.onTimerTick, self.timer)
		
		#self.create_menu() #TODO
		self.create_status_bar()
		self.create_main_panel()
		
		# Event Parser Process
		self.ready = True
		self.worker = EventParser(self)
		
		# Set up event handler for the parser thread new data
		EVT_DATA_READY(self,self.OnDataReady)
		
		self.Bind(wx.EVT_CLOSE, self.OnCloseWindow)
		
		# Start the timer
		self.timer.Start(TIMER_RATE)
	
	def OnDataReady(self, event):
		#~ print('data!')
		self.waveform.DrawWaveform(self.waveform.axes, event.data)
		self.ready=True
	
	def create_main_panel(self):
		self.panel = wx.Panel(self)
		
		self.waveform = WaveformPanel(self.panel)
		self.hist = HistPanel(self.panel)
		
	
		
		
		# Align control elements:
		self.vbox1 = wx.BoxSizer(wx.VERTICAL)
		
		cb_waveform = wx.CheckBox(self.panel,label="Waveform")
		cb_hist = wx.CheckBox(self.panel,label="Histogram")
		cb_baseline = wx.CheckBox(self.panel,label="Baseline")
		
		cb_waveform.SetValue(True)
		cb_hist.SetValue(True)
		
		self.vbox1.Add(cb_waveform, 0)
		self.vbox1.Add(cb_hist, 0)
		self.vbox1.Add(cb_baseline, 0)
		self.vbox1.AddSpacer(20)
		
		# Align controls and plots
		self.hbox = wx.BoxSizer(wx.HORIZONTAL)
		self.hbox.Add(self.vbox1, 0, flag=wx.LEFT | wx.TOP)
		self.hbox.AddSpacer(10) 
		self.hbox.Add(self.waveform, 1, flag=wx.GROW)
		self.hbox.AddSpacer(20) 
		self.hbox.Add(self.hist, 1, flag=wx.GROW)


		self.panel.SetSizer(self.hbox)
		self.hbox.SetSizeHints(self)
		
		self.hbox.Fit(self)
		
	
	def create_status_bar(self):
		self.statusbar = self.CreateStatusBar()
		self.statusbar.SetFieldsCount(2)
		self.statusbar.SetStatusWidths([-1, -2])
		
	def updateStatus(self):
		global events
		global args
		global hist
		
		events_count = len(events)
		hist_count = len(hist)
		
		self.statusbar.SetStatusText('Events: %d' % events_count, 0)
		self.statusbar.SetStatusText('Hist: %d' % hist_count, 1)
		pass
 
	def onTimerTick(self, event):
		#~ print ("updated: %s" % time.ctime())
		global events
		evt = events[-1:] 
		if evt: # If already have some events.
			#~ print(',\t'.join( map(str, integrate(evt[0], args.baseline) )))
			self.hist.DrawHist()
			
		self.updateStatus()
	
	

	def OnCloseWindow(self, event):
		if self.worker:
			self.worker.abort()
		self.Destroy()

class MainGUI(wx.App):
	def OnInit(self):
		self.frame = MainFrame(None, -1)
		self.frame.Show(True)
		self.SetTopWindow(self.frame)
		return True
		
		
def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('infile', nargs='?', type=str, default='-',
		help="raw data file (stdin by default)")
	parser.add_argument('-b','--baseline', type=int, default=20,
		help='a number of baseline samples')
	#~ parser.add_argument('--debug', action='store_true')
	
	global args
	args = parser.parse_args()

	if args.infile == '-':
		args.infile = sys.stdin
	else:
		try:
			args.infile = io.open(args.infile,  'rb', buffering=0)
		except IOError as e:
			sys.stderr.write('Err: ' + e.strerror+': "' + e.filename +'"\n')
			exit(e.errno)
	
	# TODO: nogui mode (ascii? curses?)
	
	app = MainGUI(0)
	app.MainLoop()

if __name__ == '__main__':
	main()
