from abc import ABC
from typing import List, IO

import requests
from mcdreforged.api.all import *


class AbstractFileUploader(ABC):
	def upload(self, server: ServerInterface, file: IO, file_name: str) -> str:
		raise NotImplementedError()


class FileUploaderTransferSh(AbstractFileUploader):
	URL = 'https://transfer.sh/'

	def upload(self, server: ServerInterface, file: IO, file_name: str) -> str:
		try:
			response = requests.post(self.URL, files={file_name: file})
			return response.text.strip().replace('transfer.sh/', 'transfer.sh/get/')
		except Exception as e:
			server.logger.warning('Uploading to "{}" failed: {}'.format(self.URL, e))
			raise


class FileUploaderUguu(AbstractFileUploader):
	def __init__(self, url: str):
		self.__url = url

	def upload(self, server: ServerInterface, file: IO, file_name: str) -> str:
		try:
			response = requests.post(self.__url, files={'files[]': (file_name, file)})
			js = response.json()
		except Exception as e:
			server.logger.warning('Uploading to "{}" failed: {}'.format(self.__url, e))
			raise
		try:
			# {'success': True, 'files': [{'hash': '1eba7caf09a39110ad2f542e3ed8700d1a69c6d3', 'name': 'LICENSE', 'url': 'https://a.tmp.ninja/PPgoeBqb', 'size': 35823}]}
			if js['success']:
				return js['files'][0]['url']
			else:
				raise Exception(js['description'])
		except KeyError as e:
			server.logger.warning('Unknown respond json: {} ({})'.format(js, e))
			raise


FILE_UPLOADER_LIST = [
	FileUploaderTransferSh(),
	FileUploaderUguu('https://tmp.ninja/upload.php'),
	FileUploaderUguu('https://uguu.se/upload.php'),
]  # type: List[AbstractFileUploader]
