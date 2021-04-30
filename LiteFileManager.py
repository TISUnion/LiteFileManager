import json
import os
import shutil
import threading
import time
from abc import ABC
from typing import Optional, List, Union, Dict, Callable, Any, Tuple

import requests
from mcdreforged.api.all import *

PLUGIN_METADATA = {
	'id': 'lite_file_manager',
	'name': 'Lite File Manager',
	'version': '1.0.0',
	'description': '一个轻量级的游戏内文件管理器',
	'dependencies': {
		'mcdreforged': '>=1.5.0-alpha.1'
	}
}


class OpType:
	read = 'read'
	write = 'write'


PREFIX = '!!lfm'
LOG_FILE = 'action_record.log'
CONFIG_FILE = 'config.json'
config = {
	'permission_requirement': 2,
	'max_import_size': 10 * 2 ** 20,  # 10MB
	'file_per_page': 10,
	'directories': {
		'structures': {
			'path': './server/world/generated/minecraft/structures',
			'permission': {
				OpType.read: 2,
				OpType.write: 3
			}
		}
	}
}
DEFAULT_CONFIG = config.copy()
DATA_FOLDER = None  # type: Optional[str]


def pretty_file_size(size: int) -> str:
	for c in ('B', 'KB', 'MB', 'GB', 'TB'):
		unit = c
		if size < 2 ** 10:
			break
		size /= 2 ** 10
	return str(round(size, 2)) + unit


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


action_logger = None  # type: Optional[Logger]


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
		return 'SFM file exporter for {}'.format(self._session.source)

	def __export(self, file_path: str):
		fn = os.path.basename(file_path)
		try:
			with open(file_path, 'rb') as file:
				err = None
				for url in ('https://uguu.se/upload.php', 'https://tmp.ninja/upload.php'):
					try:
						response = requests.post(url, files={'files[]': (os.path.basename(file_path), file)})
						js = response.json()
						# {'success': True, 'files': [{'hash': '1eba7caf09a39110ad2f542e3ed8700d1a69c6d3', 'name': 'LICENSE', 'url': 'https://a.tmp.ninja/PPgoeBqb', 'size': 35823}]}
						if js['success']:
							url = js['files'][0]['url']
							err = None
							break
						else:
							raise Exception(js['description'])
					except Exception as e:
						self._session.server.logger.info('Uploading to "{}" failed: {}'.format(url, e))
						err = e
				if err is not None:
					raise err
		except Exception as e:
			self._session.msg('§a{}§r导出失败: '.format(fn, e))
		else:
			self._session.msg(RTextList('§a{}§r导出成功: '.format(fn), RText(url, RColor.blue, styles=RStyle.underlined)).c(RAction.open_url, url))

	def export_file(self, file_path: str):
		self._run_async(self.__export, (file_path,))


# download a file and store it in the given path with given name from the given url
class FileImporter(AsyncWorker):
	def get_thread_name(self) -> str:
		return 'SFM file importer for {}'.format(self._session.source)

	def __import(self, directory: str, url: str, file_name: str):
		temp_file_path = os.path.join(DATA_FOLDER, self._session.get_name() + '#' + file_name)
		target_file_path = os.path.join(directory, file_name)
		try:
			response = requests.get(url, stream=True)
			total_size = 0
			oversize = False
			with open(temp_file_path, 'wb') as file_handler:
				for chunk in response.iter_content(chunk_size=4096):
					total_size += len(chunk)
					if total_size > config['max_import_size']:
						oversize = True
						break
					if chunk:
						file_handler.write(chunk)
		except Exception as e:
			self._session.msg('§a{}§r导入失败: {}'.format(file_name, e))
		else:
			if oversize:
				self._session.msg('§a{}§r超过文件大小限制{}，无法导入'.format(file_name, pretty_file_size(config['max_import_size'])))
				os.remove(temp_file_path)
			else:
				self._session.msg('§a{}§r导入成功，文件大小{}'.format(file_name, pretty_file_size(total_size)))
				shutil.move(temp_file_path, target_file_path)

	def import_file(self, directory: str, url: str, file_name: Optional[str]):
		self._run_async(self.__import, (directory, url, file_name))


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
		for mounted, info in config['directories'].items():
			if self.source.get_permission_level() >= info['permission'][OpType.read]:
				self.mounted_dirs[mounted] = info['path']

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
		return self.source.has_permission(config['directories'][mounted_dir]['permission'][OpType.write])

	def __ensure_writable(self):
		if not self.__can_do_write():
			self.msg(RText('无文件写入权限', RColor.red))
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
				name_text.h('文件大小: {}'.format(pretty_file_size(file.size)))
			else:
				hover_msg = '点击以进入目录§e{}§r'.format(fn) if file != self.DIR_TO_UPPER_FILE else '点击返回上一级目录'
				name_text.h(hover_msg).c(RAction.run_command, '{} cd {}'.format(PREFIX, json.dumps(fn)))
			msg = RTextList('  ', name_text)
			if file.is_file:
				msg.append(' ', RText('[×]', RColor.dark_red).h('删除文件§a{}§r'.format(fn)).c(RAction.suggest_command, '{} delete {}'.format(PREFIX, json.dumps(fn))))
				msg.append(' ', RText('[✎]', RColor.dark_purple).h('重命名文件§a{}§r'.format(fn)).c(RAction.suggest_command, '{} rename {} '.format(PREFIX, json.dumps(fn))))
				msg.append(' ', RText('[↓]', RColor.dark_blue).h('导出文件§a{}§r'.format(fn)).c(RAction.suggest_command, '{} export {}'.format(PREFIX, json.dumps(fn))))
			self.msg(msg)

		self.msg(RTextList(
			'当前所在目录: ',
			RText(self.current_dir, RColor.aqua), ' ',
			RText('[↑]', RColor.dark_blue).h('导入文件').c(RAction.suggest_command, '{} import '.format(PREFIX))
		))
		color_map = {False: RColor.white, True: RColor.yellow}
		color_arrow = {False: RColor.dark_gray, True: RColor.gray}
		sorted_file_list = list(sorted(file_list, key=lambda f: (f.is_file, f.name)))

		if page is not None:
			file_per_page = config['file_per_page']
			left, right = (page - 1) * file_per_page, page * file_per_page
			for i in range(left, right):
				if 0 <= i < len(sorted_file_list):
					display(sorted_file_list[i])

			has_prev = 0 < left < len(sorted_file_list)
			has_next = 0 < right < len(sorted_file_list)
			prev_page = RText('<-', color_arrow[has_prev])
			if has_prev:
				prev_page.c(RAction.run_command, '{} ls {}'.format(PREFIX, page - 1)).h('点击显示上一页')
			next_page = RText('->', color_arrow[has_next])
			if has_next:
				next_page.c(RAction.run_command, '{} ls {}'.format(PREFIX, page + 1)).h('点击显示下一页')

			self.msg(RTextList(
				prev_page,
				' 第§6{}§r/§6{}§r页 '.format(page, max(len(sorted_file_list) - 1, 0) // file_per_page + 1),
				next_page
			))
		else:
			for f in sorted_file_list:
				display(f)

		file_amount = len(list(filter(lambda x: x.is_file, file_list)))
		self.msg('共有§6{}§r个文件, §6{}§r个文件夹'.format(file_amount, len(file_list) - file_amount - 1))  # -1 for ignoring ..

	def list_file(self, page: Optional[int]):
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
		self.__display_file_list(file_list, page)

	def print_current_dir(self):
		self.msg(RTextList('当前所在目录: ', RText(self.current_dir, RColor.aqua)))
		self.msg(RTextList(
			RText('§7[§r查看当前路径文件§7]§r').c(RAction.run_command, '{} ls'.format(PREFIX)), ' ',
			RText('§7[§r返回根目录§7]§r').c(RAction.run_command, '{} cd {}'.format(PREFIX, self.ROOT))
		))

	def change_dir(self, input_dir: str):
		def jump_into(current_dir: str, dir_name: str) -> Tuple[Optional[str], Optional[str]]:
			if self.__is_at_root(current_dir):
				if dir_name == self.DIR_TO_UPPER:
					return None, '不准在根目录返回上级'
				if dir_name in self.mounted_dirs:
					next_dir = '/' + dir_name
				else:
					return None, '未知挂载文件夹§e{}§r'.format(dir_name)
			else:
				if dir_name == self.DIR_TO_UPPER:
					next_dir = current_dir.rsplit('/', 1)[0]
					if len(next_dir) == 0:
						next_dir = self.ROOT
				else:
					if os.path.isdir(os.path.join(self.__get_current_real_dir(current_dir), dir_name)):
						next_dir = current_dir + '/' + dir_name
					else:
						return None, '未知文件夹§e{}§r'.format(dir_name)
			return next_dir, None

		err = None  # type: Optional[Union[str, RTextBase]]

		# absolute path
		if input_dir.startswith('/'):
			cwd = self.ROOT
		# relative path
		else:
			cwd = self.current_dir
		for _dir in input_dir.split('/'):
			if len(_dir) > 0:
				c = self.check_char(_dir)
				if c is not None:
					err = '输入路径含非法字符§c{}§r'.format(c)
					break
				else:
					cwd, err = jump_into(cwd, _dir)
					if err is not None:
						break
		if err is not None:
			self.msg(RText(err, RColor.red))
		else:
			self.current_dir = cwd  # type: str
			self.list_file(1)

	def __check_file_name(self, file_name: str) -> bool:
		c = self.check_char(file_name)
		if c is not None:
			self.msg('§c输入文件名含非法字符{}§r'.format(c))
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
						self.msg(RText('在操作文件时出现错误: {}'.format(e), RColor.red))
				else:
					file_not_found = True
			else:
				file_not_found = True
			if file_not_found:
				self.msg('文件§a{}§r不存在'.format(file_name))

	def delete_file(self, file_name: str):
		def something(file_path: str):
			os.remove(file_path)
			self.msg('已删除§a{}§r'.format(file_name))
		action_logger.log(self.source, 'delete', file_name)
		if self.__ensure_writable():
			self.__do_something_with_file(file_name, something)

	def rename_file(self, file_name: str, new_name: str):
		def something(file_path: str):
			os.rename(file_path, os.path.join(self.__get_current_real_dir(), new_name))
			self.msg('已将§a{}§r重命名为§a{}§r'.format(file_name, new_name))
		action_logger.log(self.source, 'rename', '{} -> {}'.format(file_name, new_name))
		if self.__ensure_writable() and self.__check_file_name(new_name):
			self.__do_something_with_file(file_name, something)

	def export_file(self, file_name: str):
		def something(file_path: str):
			if self.file_exporter.is_working():
				self.msg('请等待上一个文件完成导出')
			else:
				self.msg('正在导出§a{}§r'.format(file_name))
				self.file_exporter.export_file(file_path)
		action_logger.log(self.source, 'export', file_name)
		self.__do_something_with_file(file_name, something)

	def import_file(self, url: str, file_name: Optional[str]):
		if not self.__ensure_writable():
			return
		action_logger.log(self.source, 'import', 'from {} as {}'.format(url, file_name))
		if file_name is None or self.__check_file_name(file_name):
			if self.__is_at_root():
				self.msg(RText('无法向根目录导入文件', RColor.red))
				return
			if self.file_importer.is_working():
				self.msg('请等待上一个文件完成导入')
			else:
				if file_name is None:
					file_name = os.path.basename(url)
				self.msg('正在由§9{}§r导入文件中'.format(url))
				self.msg('目标文件名: §a{}§r'.format(file_name))
				_dir = self.__get_current_real_dir()
				if os.path.exists(os.path.join(_dir, file_name)):
					self.msg('目标文件已存在')
				else:
					self.file_importer.import_file(_dir, url, file_name)


sessions = {}  # type: Dict[str, Session]


def get_session(source: CommandSource):
	key = source.player if isinstance(source, PlayerCommandSource) else '_#{}#_'.format(type(source))
	if key not in sessions:
		sessions[key] = Session(source)
	return sessions[key]


@new_thread(PLUGIN_METADATA['id'])
def session_action(source: CommandSource, func: Callable[[Session], Any]):
	try:
		func(get_session(source))
	except Exception as e:
		source.reply(RText('ERROR', RColor.red).h(str(e)))
		source.get_server().logger.exception('qwq')


def list_file(source: CommandSource, page: Optional[int]):
	session_action(source, lambda session: session.list_file(page))


def print_current_dir(source: CommandSource):
	session_action(source, lambda session: session.print_current_dir())


def change_dir(source: CommandSource, dir_name: str):
	session_action(source, lambda session: session.change_dir(dir_name))


def delete_file(source: CommandSource, file_name: str):
	session_action(source, lambda session: session.delete_file(file_name))


def rename_file(source: CommandSource, file_name: str, new_name: str):
	session_action(source, lambda session: session.rename_file(file_name, new_name))


def export_file(source: CommandSource, file_name: str):
	session_action(source, lambda session: session.export_file(file_name))


def import_file(source: CommandSource, url: str, file_name: Optional[str]):
	session_action(source, lambda session: session.import_file(url, file_name))


HELP_MESSAGES = '''
--------- {1} v{2} ---------
{3}
§7{0}§r 显示此帮助信息
§7{0} ls §6[<page>]§r 列出当前目录下的文件。可指定显示的页数
§7{0} pwd§r 显示当前所在的目录
§7{0} cd §a<path>§r 进入指定目录。目录可为相对路径，或以/开头的绝对路径
§7{0} delete §a<file_name>§r 删除当前目录下的指定文件。需要写入权限
§7{0} rename §a<file_name> <new_name>§r 重命名当前目录下的指定文件。需要写入权限
§7{0} export §a<file_name>§r 导出当前目录下的指定文件
§7{0} import §9<url> §a[<file_name>]§r 从给定url下载并导入文件至当前目录，可指定保存的文件名。需要写入权限
--- 示例 ---
§7{0} ls §61§r
§7{0} cd §astructures§r
§7{0} export §amy_struct.nbt§r
§7{0} import §9https://path.to.my/struct.nbt §anew_struct.nbt§r
'''.format(PREFIX, PLUGIN_METADATA['name'], PLUGIN_METADATA['version'], PLUGIN_METADATA['description']).strip()


def show_help(source: CommandSource):
	for line in HELP_MESSAGES.splitlines():
		source.reply(line)


def on_load(server: ServerInterface, old_inst):
	global action_logger, DATA_FOLDER
	DATA_FOLDER = server.get_data_folder()
	action_logger = Logger(server, os.path.join(DATA_FOLDER, LOG_FILE))

	load_config(server)
	register_stuffs(server)


def register_stuffs(server: ServerInterface):
	server.register_command(
		Literal(PREFIX).
		requires(lambda src: src.has_permission(config['permission_requirement']), lambda: '权限不足').
		runs(show_help).
		then(
			Literal('ls').
			runs(lambda src: list_file(src, None)).
			then(
				Integer('page').
				runs(lambda src, ctx: list_file(src, ctx['page']))
			)
		).
		then(Literal('pwd').runs(print_current_dir)).
		then(
			Literal('cd').then(
				QuotableText('path').
				runs(lambda src, ctx: change_dir(src, ctx['path']))
			)
		).
		then(
			Literal('delete').then(
				QuotableText('file_name').
				runs(lambda src, ctx: delete_file(src, ctx['file_name']))
			)
		).
		then(
			Literal('rename').then(
				QuotableText('file_name').then(
					QuotableText('new_name').
					runs(lambda src, ctx: rename_file(src, ctx['file_name'], ctx['new_name']))
				).
				on_error(UnknownCommand, lambda src: src.reply('请输入文件名'))
			)
		).
		then(
			Literal('export').then(
				QuotableText('file_name').
				runs(lambda src, ctx: export_file(src, ctx['file_name']))
			)
		).
		then(
			Literal('import').then(
				QuotableText('url').
				runs(lambda src, ctx: import_file(src, ctx['url'], None)).
				then(
					QuotableText('file_name').
					runs(lambda src, ctx: import_file(src, ctx['url'], ctx['file_name']))
				)
			).
			on_error(UnknownCommand, lambda src: src.reply('请输入URL'))
		)
	)
	server.register_help_message(PREFIX, PLUGIN_METADATA['description'], permission=config['permission_requirement'])


def load_config(server: ServerInterface):
	global config
	needs_save = False
	config_file_path = os.path.join(DATA_FOLDER, CONFIG_FILE)
	try:
		with open(config_file_path) as file:
			js = json.load(file)
	except Exception as e:
		config = DEFAULT_CONFIG.copy()
		needs_save = True
		server.logger.info('Fail to read config file, using default config ({})'.format(e))
	else:
		config = {}
		for key, value in DEFAULT_CONFIG.items():
			if key in js:
				config[key] = js[key]
			else:
				config[key] = value
				needs_save = True
				server.logger.info('Found missing config key "{}", using default value "{}"'.format(key, value))
		server.logger.info('Config file loaded')
	if needs_save:
		with open(config_file_path, 'w') as file:
			json.dump(config, file, indent=4)
