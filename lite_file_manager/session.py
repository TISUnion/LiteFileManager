import json
import os
from typing import Optional, List, Union, Dict, Callable, Any, Tuple

from mcdreforged.api.all import *

from lite_file_manager import constants, utils, common
from lite_file_manager.async_worker import FileExporter, FileImporter
from lite_file_manager.common import tr


class Session:
	ID_COUNTER = 0
	ROOT = '/'
	DIR_TO_UPPER = '..'
	ILLEGAL_CHARS = {'/', '\\', ':', '*', '?', '"', '|', '<', '>'}

	class File:
		def __init__(self, name: str, is_dir: bool, size: int):
			self.name = name
			self.is_dir = is_dir
			self.size = size

		@property
		def is_file(self) -> bool:
			return not self.is_dir

	DIR_TO_UPPER_FILE = File(DIR_TO_UPPER, True, 0)

	def __init__(self, source: CommandSource):
		self.__id = Session.ID_COUNTER
		Session.ID_COUNTER += 1
		self.source = source
		self.server = source.get_server()
		self.current_dir = self.ROOT  # starts with /, ends without '/' unless at root
		self.file_exporter = FileExporter(self)
		self.file_importer = FileImporter(self)
		self.mounted_dirs = {}  # type: Dict[str, str]
		for mounted, info in common.config.directories.items():
			if self.source.get_permission_level() >= info.permission[constants.OpType.read]:
				self.mounted_dirs[mounted] = info.path

	def get_name(self):
		return 'Session{}'.format(self.__id)

	def msg(self, message: Union[str, RTextBase]):
		self.source.reply(message)

	@staticmethod
	def __split_current_dir(current_dir: str) -> Tuple[Optional[str], Optional[str]]:
		tps = current_dir.split('/', 2)
		if len(tps) == 3:  # self.current_dir == /a/b
			_, mounted_dir, path = tps  # '', 'a', 'b'
			return mounted_dir, path
		elif len(tps) == 2:  # self.current_dir == /a
			_, mounted_dir = tps  # '', 'a'
			if len(mounted_dir) > 0:  # at root
				return mounted_dir, None
		return None, None

	def __get_current_real_dir(self, current_dir: Optional[str] = None) -> Optional[str]:
		if current_dir is None:
			current_dir = self.current_dir
		mounted_dir, path = self.__split_current_dir(current_dir)
		if mounted_dir is not None and path is not None:
			return os.path.join(self.mounted_dirs[mounted_dir], path)
		elif mounted_dir is not None:
			return self.mounted_dirs[mounted_dir]
		else:
			return None

	def __can_do_write(self):
		mounted_dir, path = self.__split_current_dir(self.current_dir)
		if mounted_dir is None:
			return False
		return self.source.has_permission(common.config.directories[mounted_dir].permission[constants.OpType.write])

	def __ensure_writable(self):
		if not self.__can_do_write():
			self.msg(RText(tr('session.no_write_permission'), RColor.red))
			return False
		return True

	def __is_at_root(self, current_dir: Optional[str] = None):
		if current_dir is None:
			current_dir = self.current_dir
		return current_dir == self.ROOT

	@classmethod
	def check_char(cls, s: str) -> Optional[str]:
		for c in cls.ILLEGAL_CHARS:
			if c in s:
				return c
		return None

	def __display_file_list(self, file_list: List[File], page: Optional[int]):
		def display(file: Session.File):
			fn = file.name
			name_text = RText(fn if file.is_file else fn + '/', color_map[file.is_dir])
			if file.is_file:
				name_text.h(tr('session.ls.file_size', utils.pretty_file_size(file.size)))
			else:
				hover_msg = tr('session.ls.enter_dir', fn) if file != self.DIR_TO_UPPER_FILE else tr('session.ls.enter_parent')
				name_text.h(hover_msg).c(RAction.run_command, '{} cd {}'.format(constants.PREFIX, json.dumps(fn)))
			msg = RTextList('  ', name_text)
			if file.is_file:
				msg.append(' ', RText('[×]', RColor.dark_red).h(tr('session.ls.delete', fn)).c(RAction.suggest_command, '{} delete {}'.format(constants.PREFIX, json.dumps(fn))))
				msg.append(' ', RText('[✎]', RColor.dark_purple).h(tr('session.ls.rename', fn)).c(RAction.suggest_command, '{} rename {} '.format(constants.PREFIX, json.dumps(fn))))
				msg.append(' ', RText('[↓]', RColor.dark_blue).h(tr('session.ls.export', fn)).c(RAction.suggest_command, '{} export {}'.format(constants.PREFIX, json.dumps(fn))))
			self.msg(msg)

		self.msg(RTextList(
			tr('session.ls.current_dir'),
			RText(self.current_dir, RColor.aqua), ' ',
			RText('[>]', RColor.dark_purple).h(tr('session.ls.search')).c(RAction.suggest_command, '{} search '.format(constants.PREFIX)), ' ',
			RText('[↑]', RColor.dark_blue).h(tr('session.ls.import')).c(RAction.suggest_command, '{} import '.format(constants.PREFIX))
		))
		color_map = {False: RColor.white, True: RColor.yellow}
		color_arrow = {False: RColor.dark_gray, True: RColor.gray}
		sorted_file_list = list(sorted(file_list, key=lambda x: (x.is_file, x.name)))

		if page is not None:
			file_per_page = common.config.file_per_page
			left, right = (page - 1) * file_per_page, page * file_per_page
			for i in range(left, right):
				if 0 <= i < len(sorted_file_list):
					display(sorted_file_list[i])

			has_prev = 0 < left < len(sorted_file_list)
			has_next = 0 < right < len(sorted_file_list)
			prev_page = RText('<-', color_arrow[has_prev])
			if has_prev:
				prev_page.c(RAction.run_command, '{} ls {}'.format(constants.PREFIX, page - 1)).h(tr('session.ls.page.prev'))
			next_page = RText('->', color_arrow[has_next])
			if has_next:
				next_page.c(RAction.run_command, '{} ls {}'.format(constants.PREFIX, page + 1)).h(tr('session.ls.page.next'))

			self.msg(RTextList(
				prev_page,
				' {} '.format(tr('session.ls.page.footer', page, max(len(sorted_file_list) - 1, 0) // file_per_page + 1)),
				next_page
			))
		else:
			for f in sorted_file_list:
				display(f)

		file_amount = len(list(filter(lambda x: x.is_file, file_list)))
		self.msg(tr('session.ls.summary', file_amount, len(file_list) - file_amount - (1 if self.DIR_TO_UPPER_FILE in file_list else 0)))

	def list_file(self, keyword: Optional[str], page: Optional[int]):
		file_list = [self.DIR_TO_UPPER_FILE]  # type: List[Session.File]
		if self.__is_at_root():
			for mounted in self.mounted_dirs.keys():
				file_list.append(Session.File(mounted, True, 0))
		else:
			cwd = self.__get_current_real_dir()
			try:
				ls_result = os.listdir(cwd)
			except FileNotFoundError:
				ls_result = []
			for name in ls_result:
				full_path = os.path.join(cwd, name)
				file_list.append(Session.File(name, os.path.isdir(full_path), os.path.getsize(full_path)))
		if keyword is not None:
			file_list = list(filter(lambda f: keyword in f.name, file_list))
		self.__display_file_list(file_list, page)

	def print_current_dir(self):
		self.msg(RTextList(tr('session.ls.current_dir'), RText(self.current_dir, RColor.aqua)))
		self.msg(RTextList(
			RText(tr('session.pwd.see_file')).c(RAction.run_command, '{} ls'.format(constants.PREFIX)), ' ',
			RText(tr('session.pwd.to_root')).c(RAction.run_command, '{} cd {}'.format(constants.PREFIX, self.ROOT))
		))

	def change_dir(self, input_path: str):
		def jump_into(current_dir: str, path: str) -> Tuple[Optional[str], Optional[RTextBase]]:
			if self.__is_at_root(current_dir):
				if path == self.DIR_TO_UPPER:
					return None, tr('session.cd.at_root')
				if path in self.mounted_dirs:
					next_dir = '/' + path
				else:
					return None, tr('session.cd.unknown_mounted', path)
			else:
				if path == self.DIR_TO_UPPER:
					next_dir = current_dir.rsplit('/', 1)[0]
					if len(next_dir) == 0:
						next_dir = self.ROOT
				else:
					if os.path.isdir(os.path.join(self.__get_current_real_dir(current_dir), path)):
						next_dir = current_dir + '/' + path
					else:
						return None, tr('session.cd.unknown_dir', path)
			return next_dir, None

		self.msg(tr('session.cd.enter', input_path))
		err = None  # type: Optional[Union[str, RTextBase]]

		# absolute path
		if input_path.startswith('/'):
			cwd = self.ROOT
		# relative path
		else:
			cwd = self.current_dir
		for sub_path in input_path.split('/'):
			if len(sub_path) > 0:
				c = self.check_char(sub_path)
				if c is not None:
					err = tr('session.cd.illegal_char', c)
					break
				else:
					cwd, err = jump_into(cwd, sub_path)
					if err is not None:
						break
		if err is not None:
			self.msg(err.set_color(RColor.red))
		else:
			self.current_dir = cwd  # type: str
			self.list_file(None, 1)

	def __check_file_name(self, file_name: str) -> bool:
		c = self.check_char(file_name)
		if c is not None:
			self.msg(tr('session.cd.illegal_char', c))
			return False
		return True

	def __do_something_with_file(self, file_name: str, consumer: Callable[[str], Any]):
		if self.__check_file_name(file_name):
			file_not_found = False
			if not self.__is_at_root():
				file_path = os.path.join(self.__get_current_real_dir(), file_name)
				if os.path.isfile(file_path):
					try:
						consumer(file_path)
					except Exception as e:
						self.msg(RText(tr('session.mani_file.error', file_name, e), RColor.red))
				else:
					file_not_found = True
			else:
				file_not_found = True
			if file_not_found:
				self.msg(tr('session.mani_file.not_found', file_name))

	def delete_file(self, file_name: str):
		def something(file_path: str):
			os.remove(file_path)
			self.msg(tr('session.delete', file_name))
		common.action_logger.log(self.source, 'delete', file_name)
		if self.__ensure_writable():
			self.__do_something_with_file(file_name, something)

	def rename_file(self, file_name: str, new_name: str):
		def something(file_path: str):
			os.rename(file_path, os.path.join(self.__get_current_real_dir(), new_name))
			self.msg(tr('session.rename', file_name, new_name))
		common.action_logger.log(self.source, 'rename', '{} -> {}'.format(file_name, new_name))
		if self.__ensure_writable() and self.__check_file_name(new_name):
			self.__do_something_with_file(file_name, something)

	def export_file(self, file_name: str):
		def something(file_path: str):
			if self.file_exporter.is_working():
				self.msg(tr('session.export.wait'))
			else:
				self.msg(tr('session.export.message', file_name))
				self.file_exporter.export_file(file_path)
		common.action_logger.log(self.source, 'export', file_name)
		self.__do_something_with_file(file_name, something)

	def import_file(self, url: str, file_name: Optional[str]):
		if not self.__ensure_writable():
			return
		common.action_logger.log(self.source, 'import', 'from {} as {}'.format(url, file_name))
		if file_name is None or self.__check_file_name(file_name):
			if self.__is_at_root():
				self.msg(RText(tr('session.import.at_root'), RColor.red))
				return
			if self.file_importer.is_working():
				self.msg(tr('session.import.wait'))
			else:
				if file_name is None:
					file_name = os.path.basename(url)
				self.msg(tr('session.import.message.0', url))
				self.msg(tr('session.import.message.1', file_name))
				_dir = self.__get_current_real_dir()
				if os.path.exists(os.path.join(_dir, file_name)):
					self.msg(tr('session.import.file_existed'))
				else:
					self.file_importer.import_file(_dir, url, file_name)
