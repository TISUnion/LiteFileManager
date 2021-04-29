import json
import os
import threading
from abc import ABC
from typing import Optional, List, Union, Dict, Callable, Any, Tuple, Iterable

import requests
from mcdreforged.api.all import *

PLUGIN_METADATA = {
	'id': 'simple_file_manager',
	'name': 'Simple File Manager',
	'version': '0.0.1',
	'description': '一个简易的游戏内文件管理器',
}


PREFIX = '!!sfm'
CONFIG_FILE = os.path.join('config', 'SimpleFileManager.json')
config = {
	'permission_level': 2,
	'directories': {
		'structures': {
			'path': './server/world/generated/minecraft/structures',
			'permission_level': 2
		}
	}
}
DEFAULT_CONFIG = config.copy()


def get_alias_dirs() -> List[str]:
	return list(config['directories'].keys())


def get_real_path_from_alias(alias_dir: str) -> str:
	ret = config['directories'].get(alias_dir)
	if ret is None:
		raise ValueError('Unknown alias_dir "{}". Avaliable alias_dir: {}'.format(alias_dir, get_alias_dirs()))
	return ret['path']


def pretty_file_size(size: int) -> str:
	for c in ('B', 'KB', 'MB', 'GB', 'TB'):
		unit = c
		if size < 2 ** 10:
			break
		size /= 2 ** 10
	return str(round(size, 2)) + unit


# upload the given file to a temporary cloud storage for user to download
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
class FileUploader(AsyncWorker):
	def get_thread_name(self) -> str:
		return 'SFM file uploader for {}'.format(self._session.player)

	def __upload(self, file_path: str):
		fn = os.path.basename(file_path)
		try:
			with open(file_path, 'rb') as file:
				response = requests.post('https://tmp.ninja/upload.php', files={'files[]': (os.path.basename(file_path), file)})
				js = response.json()
				# {'success': True, 'files': [{'hash': '1eba7caf09a39110ad2f542e3ed8700d1a69c6d3', 'name': 'LICENSE', 'url': 'https://a.tmp.ninja/PPgoeBqb', 'size': 35823}]}
				if js['success']:
					url = js['files'][0]['url']
				else:
					raise Exception(js['description'])
		except Exception as e:
			self._session.msg('{}导出失败: '.format(fn, e))
		else:
			self._session.msg(RTextList('{}导出成功: '.format(fn), RText(url, color=RColor.blue, styles=RStyle.underlined)).c(RAction.open_url, url))

	def upload(self, file_path: str):
		self._run_async(self.__upload, (file_path,))


# upload the given file to a temporary cloud storage for user to download
class FileDownloader(AsyncWorker):
	def get_thread_name(self) -> str:
		return 'SFM file downloader for {}'.format(self._session.player)

	def __download(self, file_path: str, url: str, file_name: str):
		try:
			response = requests.get(url)
			with open(os.path.join(file_path, file_name), 'wb') as file_handler:
				file_handler.write(response.content)
		except Exception as e:
			self._session.msg('{}导入失败: '.format(file_name, e))
		else:
			self._session.msg('{}导入成功'.format(file_name))

	def download(self, file_path: str, url: str, file_name: Optional[str]):
		self._run_async(self.__download, (file_path, url, file_name))


class Session:
	ROOT = '/'
	DIR_TO_UPPER = '..'

	class File:
		def __init__(self, name: str, is_dir: bool, size: int):
			self.name = name
			self.is_dir = is_dir
			self.size = size

		@property
		def is_file(self) -> bool:
			return not self.is_dir

	def __init__(self, server: ServerInterface, player: str):
		self.server = server
		self.player = player
		self.current_dir = self.ROOT  # starts with /, ends without '/' unless at root
		self.file_uploader = FileUploader(self)
		self.file_downloader = FileDownloader(self)

	def msg(self, message: Union[str, RTextBase]):
		self.server.tell(self.player, message)

	def __get_current_real_dir(self) -> Optional[str]:
		tps = self.current_dir.split('/', 2)
		if len(tps) == 3:  # self.current_dir == /a/b
			_, alias_dir, path = tps  # '', 'a', 'b'
			return os.path.join(get_real_path_from_alias(alias_dir), path)
		elif len(tps) == 2:  # self.current_dir == /a
			_, alias_dir = tps  # '', 'a'
			return get_real_path_from_alias(alias_dir)
		else:
			return None

	def __is_at_root(self, current_dir: Optional[str] = None):
		if current_dir is None:
			current_dir = self.current_dir
		return current_dir == self.ROOT

	@staticmethod
	def check_char(s: str, char_set: Iterable[str]) -> Optional[str]:
		for c in char_set:
			if c in s:
				return c
		return None

	def __display_file_list(self, file_list: List[File]):
		file_amount = len(list(filter(lambda f: f.is_file, file_list)))
		self.msg('当前目录: §b{}§r; 共有§6{}§r个文件, §6{}§r个文件夹'.format(self.current_dir, file_amount, len(file_list) - file_amount))
		color_map = {False: RColor.white, True: RColor.yellow}
		for file in sorted(file_list, key=lambda f: (f.is_file, f.name)):
			fn = file.name
			name_text = RText(fn if file.is_file else fn + '/', color=color_map[file.is_dir])
			if file.is_file:
				name_text.h('文件大小: {}'.format(pretty_file_size(file.size)))
			if file.is_dir:
				name_text.h('点击以进入目录§e{}§r'.format(fn)).c(RAction.run_command, '{} cd {}'.format(PREFIX, json.dumps(fn)))
			msg = RTextList(name_text)
			if file.is_file:
				msg.append(' ', RText('[删除]', color=RColor.red).h('删除文件{}'.format(fn)).c(RAction.suggest_command, '{} delete {}'.format(PREFIX, json.dumps(fn))))
				msg.append(' ', RText('[导出]', color=RColor.blue).h('导出文件{}'.format(fn)).c(RAction.suggest_command, '{} export {}'.format(PREFIX, json.dumps(fn))))
			self.msg(msg)

	def list_file(self):
		file_list = [Session.File(self.DIR_TO_UPPER, True, 0)]  # type: List[Session.File]
		if self.__is_at_root():
			for alias in get_alias_dirs():
				file_list.append(Session.File(alias, True, 0))
		else:
			cwd = self.__get_current_real_dir()
			try:
				ls_result = os.listdir(cwd)
			except FileNotFoundError:
				ls_result = []
			for name in ls_result:
				full_path = os.path.join(cwd, name)
				file_list.append(Session.File(name, os.path.isdir(full_path), os.path.getsize(full_path)))
		self.__display_file_list(file_list)

	def print_current_dir(self):
		self.msg(RTextList('当前路径为: ', RText(self.current_dir, color=RColor.aqua)))
		self.msg(RTextList(
			RText('§7[§r查看当前路径文件§7]§r').c(RAction.run_command, '{} ls'.format(PREFIX)), ' ',
			RText('§7[§r返回根目录§7]§r').c(RAction.run_command, '{} cd {}'.format(PREFIX, self.ROOT))
		))

	def change_dir(self, input_dir: str):
		def swt(current_dir: str, dir_name: str) -> Tuple[Optional[str], Optional[str]]:
			if self.__is_at_root(current_dir):
				if dir_name == self.DIR_TO_UPPER:
					return None, '不准在根目录返回上级'
				if dir_name in get_alias_dirs():
					next_dir = '/' + dir_name
				else:
					return None, '未知文件夹化名"§b{}§r"'.format(dir_name)
			else:
				if dir_name == self.DIR_TO_UPPER:
					next_dir = current_dir.rsplit('/', 1)[0]
					if len(next_dir) == 0:
						next_dir = self.ROOT
				else:
					if os.path.isdir(os.path.join(self.__get_current_real_dir(), dir_name)):
						next_dir = current_dir + '/' + dir_name
					else:
						return None, '未知文件夹'
			return next_dir, None

		cwd = None  # type: Optional[str]
		err = None  # type: Optional[Union[str, RTextBase]]

		def check_legal(*char_set) -> bool:
			c = self.check_char(input_dir, char_set)
			if c is not None:
				nonlocal err
				err = '输入路径含非法字符"{}"'.format(c)
				return False
			return True

		# absolute path
		if input_dir.startswith('/'):
			if check_legal('\\'):
				cwd = self.ROOT
				for _dir in input_dir.split('/'):
					if len(_dir) > 0:
						cwd, err = swt(cwd, _dir)
						if err is not None:
							break
		# relative path, supported direct folder only
		else:
			if check_legal('/', '\\'):
				cwd, err = swt(self.current_dir, input_dir)
		if err is not None:
			self.msg(err)
		else:
			self.current_dir = cwd  # type: str
			self.print_current_dir()

	def __check_file_name(self, file_name: str) -> bool:
		c = self.check_char(file_name, ('/', '\\'))
		if c is not None:
			self.msg('输入文件名含非法字符{}'.format(c))
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
						self.msg(RText('在操作文件时出现错误: {}'.format(e), color=RColor.red))
				else:
					file_not_found = True
			else:
				file_not_found = True
			if file_not_found:
				self.msg('文件{}不存在'.format(file_name))

	def delete_file(self, file_name: str):
		def something(file_path: str):
			os.remove(file_path)
			self.msg('已删除{}'.format(file_name))
		self.__do_something_with_file(file_name, something)

	def rename_file(self, file_name: str, new_name: str):
		def something(file_path: str):
			os.rename(file_path, os.path.join(self.__get_current_real_dir(), new_name))
			self.msg('已将{}重命名为{}'.format(file_name, new_name))
		if self.__check_file_name(new_name):
			self.__do_something_with_file(file_name, something)

	def export_file(self, file_name: str):
		def something(file_path: str):
			if self.file_uploader.is_working():
				self.msg('请等待上一个文件完成导出')
			else:
				self.msg('正在上传{}'.format(file_name))
				self.file_uploader.upload(file_path)
		self.__do_something_with_file(file_name, something)

	def import_file(self, url: str, file_name: Optional[str]):
		if file_name is None or self.__check_file_name(file_name):
			if self.__is_at_root():
				self.msg('不可向根目录导入文件')
				return
			if self.file_downloader.is_working():
				self.msg('请等待上一个文件完成导入')
			else:
				if file_name is None:
					file_name = os.path.basename(url)
				self.msg('正在由{}导入文件中'.format(url))
				self.msg('目标文件名: {}'.format(file_name))
				self.file_downloader.download(self.__get_current_real_dir(), url, file_name)


sessions = {}  # type: Dict[str, Session]


def get_session(source: PlayerCommandSource):
	if source.player not in sessions:
		sessions[source.player] = Session(source.get_server(), source.player)
	return sessions[source.player]


@new_thread(PLUGIN_METADATA['id'])
def action(source: CommandSource, func: Callable[[Session], Any]):
	if isinstance(source, PlayerCommandSource):
		try:
			func(get_session(source))
		except Exception as e:
			source.reply(RText('ERROR', color=RColor.red).h(str(e)))
			source.get_server().logger.exception('qwq')
	else:
		source.reply('不支持非玩家输入')


def list_file(source: CommandSource):
	action(source, lambda session: session.list_file())


def print_current_dir(source: CommandSource):
	action(source, lambda session: session.print_current_dir())


def change_dir(source: CommandSource, dir_name: str):
	action(source, lambda session: session.change_dir(dir_name))


def delete_file(source: CommandSource, file_name: str):
	action(source, lambda session: session.delete_file(file_name))


def rename_file(source: CommandSource, file_name: str, new_name: str):
	action(source, lambda session: session.rename_file(file_name, new_name))


def export_file(source: CommandSource, file_name: str):
	action(source, lambda session: session.export_file(file_name))


def import_file(source: CommandSource, url: str, file_name: Optional[str]):
	action(source, lambda session: session.import_file(url, file_name))


HELP_MESSAGES = {
	'': ['显示帮助信息'],
	'ls': ['列出当前目录下的文件'],
	'pwd': ['列出当前所在的路径'],
	'cd <path>': [
		'进入指定目录'
	],
	'delete <file_name>': [
		'删除当前目录下的指定文件'
	],
	'rename <file_name> <new_name>': [
		'重命名当前目录下的指定文件'
	],
	'export <file_name>': [
		'导出当前目录下的指定文件'
	],
	'import <url> [<file_name>]': [
		'从给定url下载文件至当前目录，可指定保存的文件名'
	],
}


def show_help(source: CommandSource):
	source.reply('======= {} v{} ========'.format(PLUGIN_METADATA['name'], PLUGIN_METADATA['version']))
	source.reply(PLUGIN_METADATA['description'])
	for cmd, helps in HELP_MESSAGES.items():
		source.reply(RTextList(
			RText('{}{}{}'.format(PREFIX, ' ' if len(cmd) > 0 else '', cmd), color=RColor.gray),
			' {}'.format(helps[0])
		))
		for extra_help in helps[1:]:
			source.reply('    {}'.format(extra_help))


def on_load(server: ServerInterface, old_inst):
	load_config(server)
	register_stuffs(server)


def register_stuffs(server: ServerInterface):
	server.register_command(
		Literal(PREFIX).
		requires(lambda src: src.has_permission(config['permission_level']), lambda: '权限不足').
		runs(show_help).
		then(Literal({'list', 'ls'}).runs(list_file)).
		then(Literal({'pwd'}).runs(print_current_dir)).
		then(
			Literal({'cd'}).then(
				QuotableText('path').
				runs(lambda src, ctx: change_dir(src, ctx['path']))
			)
		).
		then(
			Literal({'delete'}).then(
				QuotableText('file_name').
				runs(lambda src, ctx: delete_file(src, ctx['file_name']))
			)
		).
		then(
			Literal({'rename'}).then(
				QuotableText('file_name').then(
					QuotableText('new_name').
					runs(lambda src, ctx: rename_file(src, ctx['file_name'], ctx['new_name']))
				)
			)
		).
		then(
			Literal({'export'}).then(
				QuotableText('file_name').
				runs(lambda src, ctx: export_file(src, ctx['file_name']))
			)
		).
		then(
			Literal({'import'}).then(
				QuotableText('url').
				runs(lambda src, ctx: import_file(src, ctx['url'], None)).
				then(
					QuotableText('file_name').
					runs(lambda src, ctx: import_file(src, ctx['url'], ctx['file_name']))
				)
			)
		)
	)


def load_config(server: ServerInterface):
	global config
	try:
		config = {}
		with open(CONFIG_FILE) as file:
			js = json.load(file)
		for key in DEFAULT_CONFIG.keys():
			config[key] = js[key]
		server.logger.info('Config file loaded')
	except Exception as e:
		config = DEFAULT_CONFIG
		with open(CONFIG_FILE, 'w') as file:
			json.dump(config, file, indent=4)
		server.logger.info('Fail to read config file, using default value ({})'.format(e))
