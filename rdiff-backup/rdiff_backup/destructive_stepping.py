from __future__ import generators
execfile("rorpiter.py")

#######################################################################
#
# destructive-stepping - Deal with side effects from traversing trees
#

class DSRPathError(Exception):
	"""Exception used when a DSRPath can't get sufficient permissions"""
	pass

class DSRPath(RPath):
	"""Destructive Stepping RPath

	Sometimes when we traverse the directory tree, even when we just
	want to read files, we have to change things, like the permissions
	of a file or directory in order to read it, or the file's access
	times.  This class is like an RPath, but the permission and time
	modifications are delayed, so that they can be done at the very
	end when they won't be disturbed later.

	Here are the new class variables:
	delay_perms - true iff future perm changes should be delayed
	newperms - holds the perm values while they are delayed
	delay_atime - true iff some atime change are being delayed
	newatime - holds the new atime
	delay_mtime - true if some mtime change is being delayed
	newmtime - holds the new mtime

	"""
	def __init__(self, source, *args):
		"""Initialize DSRP

		Source should be true iff the DSRPath is taken from the
		"source" partition and thus settings like
		Globals.change_source_perms should be paid attention to.

		If args is [rpath], return the dsrpath equivalent of rpath,
		otherwise use the same arguments as the RPath initializer.

		"""
		if len(args) == 2 and isinstance(args[0], RPath):
			rp = args[0]
			RPath.__init__(self, rp.conn, rp.base, rp.index)
		else: RPath.__init__(self, *args)

		self.set_delays(source)
		self.set_init_perms(source)

	def set_delays(self, source):
		"""Delay writing permissions and times where appropriate"""
		if not source or Globals.change_source_perms:
			self.delay_perms, self.newperms = 1, None
		else: self.delay_perms = None

		if Globals.preserve_atime:
			self.delay_atime = 1
			# Now get atime right away if possible
			if self.data.has_key('atime'): self.newatime = self.data['atime']
			else: self.newatime = None
		
		if source:
			self.delay_mtime = None # we'll never change mtime of source file
		else:
			self.delay_mtime = 1
			# Save mtime now for a dir, because it might inadvertantly change
			if self.isdir(): self.newmtime = self.getmtime()
			else: self.newmtime = None

	def set_init_perms(self, source):
		"""If necessary, change permissions to ensure access"""
		if self.isreg() and not self.readable():
			if not source or Globals.change_source_perms and self.isowner():
				self.chmod_bypass(0400)
			else: self.warn("No read permissions")
		elif self.isdir():
			if source and (not self.readable() or self.executable()):
				if Globals.change_source_perms and self.isowner():
					self.chmod_bypass(0500)
				else: warn("No read or exec permission")
			elif not source and not self.hasfullperms():
				self.chmod_bypass(0700)

	def warn(self, err):
		Log("Received error '%s' when dealing with file %s, skipping..."
			% (err, self.path), 1)
		raise DSRPathError(self.path)

	def __getstate__(self):
		"""Return picklable state.  See RPath __getstate__."""
		assert self.conn is Globals.local_connection # Can't pickle a conn
		pickle_dict = {}
		for attrib in ['index', 'data', 'delay_perms', 'newperms',
					   'delay_atime', 'newatime',
					   'delay_mtime', 'newmtime',
					   'path', 'base']:
			if self.__dict__.has_key(attrib):
				pickle_dict[attrib] = self.__dict__[attrib]
		return pickle_dict

	def __setstate__(self, pickle_dict):
		"""Set state from object produced by getstate"""
		self.conn = Globals.local_connection
		for attrib in pickle_dict.keys():
			self.__dict__[attrib] = pickle_dict[attrib]

	def chmod(self, permissions):
		"""Change permissions, delaying if self.perms_delayed is set"""
		if self.delay_perms: self.newperms = self.data['perms'] = permissions
		else: RPath.chmod(self, permissions)

	def chmod_bypass(self, permissions):
		"""Change permissions without updating the data dictionary"""
		self.delay_perms = 1
		if self.newperms is None: self.newperms = self.getperms()
		self.conn.os.chmod(self.path, permissions)

	def settime(self, accesstime, modtime):
		"""Change times, delaying if self.times_delayed is set"""
		if self.delay_atime: self.newatime = self.data['atime'] = accesstime
		if self.delay_mtime: self.newmtime = self.data['mtime'] = modtime

		if not self.delay_atime or not self.delay_mtime:
			RPath.settime(self, accesstime, modtime)
		
	def setmtime(self, modtime):
		"""Change mtime, delaying if self.times_delayed is set"""
		if self.delay_mtime: self.newmtime = self.data['mtime'] = modtime
		else: RPath.setmtime(self, modtime)

	def write_changes(self):
		"""Write saved up permission/time changes"""
		if not self.lstat(): return # File has been deleted in meantime

		if self.delay_perms and self.newperms is not None:
			RPath.chmod(self, self.newperms)

		do_atime = self.delay_atime and self.newatime is not None
		do_mtime = self.delay_mtime and self.newmtime is not None
		if do_atime and do_mtime:
			RPath.settime(self, self.newatime, self.newmtime)
		elif do_atime and not do_mtime:
			RPath.settime(self, self.newatime, self.getmtime())
		elif not do_atime and do_mtime:
			RPath.setmtime(self, self.newmtime)


class DestructiveStepping:
	"""Destructive stepping"""
	def initialize(dsrpath, source):
		"""Change permissions of dsrpath, possibly delay writes

		Abort if we need to access something and can't.  If the file
		is on the source partition, just log warning and return true.
		Return false if everything good to go.

		"""
		def abort():
			Log.FatalError("Missing access to file %s - aborting." %
						   dsrpath.path)

		def try_chmod(perms):
			"""Try to change the perms.  If fail, return error."""
			try: dsrpath.chmod_bypass(perms)
			except os.error, err: return err
			return None

		if dsrpath.isreg() and not dsrpath.readable():
			if source:
				if Globals.change_source_perms and dsrpath.isowner():
					err = try_chmod(0400)
					if err:
						warn(err)
						return 1
				else:
					warn("No read permissions")
					return 1
			elif not Globals.change_mirror_perms or try_chmod(0600): abort()
		elif dsrpath.isdir():
			if source and (not dsrpath.readable() or not dsrpath.executable()):
				if Globals.change_source_perms and dsrpath.isowner():
					err = try_chmod(0500)
					if err:
						warn(err)
						return 1
				else:
					warn("No read or exec permissions")
					return 1
			elif not source and not dsrpath.hasfullperms():
				if Globals.change_mirror_perms: try_chmod(0700)

MakeStatic(DestructiveStepping)


class DestructiveSteppingFinalizer(IterTreeReducer):
		"""Finalizer that can work on an iterator of dsrpaths

		The reason we have to use an IterTreeReducer is that some files
		should be updated immediately, but for directories we sometimes
		need to update all the files in the directory before finally
		coming back to it.

		"""
		def start_process(self, index, dsrpath):
			self.dsrpath = dsrpath

		def end_process(self):
			self.dsrpath.write_changes()



