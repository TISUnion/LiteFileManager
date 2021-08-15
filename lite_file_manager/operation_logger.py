import threading
import time

from mcdreforged.api.all import *


class Logger:
	def __init__(self, server: ServerInterface, log_file_path: str):
		self.server = server
		self.log_file_path = log_file_path
		self.write_lock = threading.Lock()

	def log(self, source: CommandSource, action: str, info: str):
		with self.write_lock:
			try:
				with open(self.log_file_path, 'a') as log_file:
					time_info = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))
					log_file.write('[{}] {} {} {}\n'.format(time_info, source, action, info))
			except Exception as e:
				self.server.logger.error('Fail to write into log file "{}": {}'.format(self.log_file_path, e))
