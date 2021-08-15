import os
import shutil
import threading
from abc import ABC
from typing import Optional, Callable, Tuple, TYPE_CHECKING

import requests
from mcdreforged.api.all import *

from lite_file_manager import file_uploader, utils, common
from lite_file_manager.common import tr

if TYPE_CHECKING:
	from lite_file_manager.session import Session


class AsyncWorker(ABC):
	def __init__(self, session: 'Session'):
		self._session = session
		self.__working = False

	def is_working(self):
		return self.__working

	def get_thread_name(self) -> str:
		raise NotImplementedError()

	def _run_async(self, target: Callable, args: Tuple):
		def task():
			self.__working = True
			target(*args)
			self.__working = False
		thread = threading.Thread(target=task, name=self.get_thread_name())
		thread.setDaemon(True)
		thread.start()


# upload the given file to a temporary cloud storage for user to download
class FileExporter(AsyncWorker):
	def get_thread_name(self) -> str:
		return 'LFM file exporter for {}'.format(self._session.source)

	def __export(self, file_path: str):
		file_name = os.path.basename(file_path)
		try:
			with open(file_path, 'rb') as file:
				err = None
				for uploader in file_uploader.FILE_UPLOADER_LIST:
					try:
						url = uploader.upload(self._session.server, file, file_name)
						break
					except Exception as e:
						err = e
				else:
					raise err
		except Exception as e:
			self._session.msg(tr('export.failed', file_name, e))
		else:
			self._session.server.logger.info('File {} ({}) has been uploaded to {}'.format(file_name, file_path, url))
			self._session.msg(RTextList(
				RText(tr('export.succeed', file_name)).h(tr('export.fill_chat')).c(RAction.suggest_command, url),
				RText(url, RColor.blue, styles=RStyle.underlined).h(tr('export.open_url')).c(RAction.open_url, url)
			))

	def export_file(self, file_path: str):
		self._run_async(self.__export, (file_path,))


# download a file and store it in the given path with given name from the given url
class FileImporter(AsyncWorker):
	def get_thread_name(self) -> str:
		return 'LFM file importer for {}'.format(self._session.source)

	def __import(self, directory: str, url: str, file_name: str):
		temp_file_path = os.path.join(common.server_inst.get_data_folder(), self._session.get_name() + '#' + file_name)
		target_file_path = os.path.join(directory, file_name)
		try:
			response = requests.get(url, stream=True)
			total_size = 0
			oversize = False
			with open(temp_file_path, 'wb') as file_handler:
				for chunk in response.iter_content(chunk_size=4096):
					total_size += len(chunk)
					if total_size > common.config.max_import_size:
						oversize = True
						break
					if chunk:
						file_handler.write(chunk)
		except Exception as e:
			self._session.msg(tr('import.failed', file_name, e))
		else:
			if oversize:
				self._session.msg(tr('import.too_large', file_name, utils.pretty_file_size(common.config.max_import_size)))
				os.remove(temp_file_path)
			else:
				self._session.msg(tr('import.succeed', file_name, utils.pretty_file_size(total_size)))
				shutil.move(temp_file_path, target_file_path)

	def import_file(self, directory: str, url: str, file_name: Optional[str]):
		self._run_async(self.__import, (directory, url, file_name))
