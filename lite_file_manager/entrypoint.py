import os
import re
from typing import Optional, Dict, Callable, Any

from mcdreforged.api.all import *

from lite_file_manager import constants, common
from lite_file_manager.common import tr
from lite_file_manager.config import Configure
from lite_file_manager.operation_logger import Logger
from lite_file_manager.session import Session

METADATA = None  # type: Optional[Metadata]
sessions = {}  # type: Dict[str, Session]


# ------------------------
# |  Session Operations  |
# ------------------------

def get_session(source: CommandSource):
	key = source.player if isinstance(source, PlayerCommandSource) else '_#{}#_'.format(type(source))
	if key not in sessions:
		sessions[key] = Session(source)
	return sessions[key]


def session_action(source: CommandSource, func: Callable[[Session], Any]):
	@new_thread(METADATA.id)
	def inner():
		try:
			func(get_session(source))
		except Exception as e:
			source.reply(RText('ERROR', RColor.red).h(str(e)))
			source.get_server().logger.exception('error')

	inner()


def list_file(source: CommandSource, page: Optional[int]):
	session_action(source, lambda s: s.list_file(None, page))


def search_file(source: CommandSource, keyword: str, page: Optional[int]):
	session_action(source, lambda s: s.list_file(keyword, page))


def print_current_dir(source: CommandSource):
	session_action(source, lambda s: s.print_current_dir())


def change_dir(source: CommandSource, dir_name: str):
	session_action(source, lambda s: s.change_dir(dir_name))


def delete_file(source: CommandSource, file_name: str):
	session_action(source, lambda s: s.delete_file(file_name))


def rename_file(source: CommandSource, file_name: str, new_name: str):
	session_action(source, lambda s: s.rename_file(file_name, new_name))


def export_file(source: CommandSource, file_name: str):
	session_action(source, lambda s: s.export_file(file_name))


def import_file(source: CommandSource, url: str, file_name: Optional[str]):
	session_action(source, lambda s: s.import_file(url, file_name))


def show_help(source: CommandSource):
	help_msg_rtext = RTextList()
	symbol = 0
	help_messages = tr('help_message', prefix=constants.PREFIX, name=METADATA.name, version=METADATA.version, description=METADATA.get_description(source.get_server().get_mcdr_language())).strip()
	for line in help_messages.splitlines(True):
		result = re.search(r'(?<=§7)' + constants.PREFIX + r'[\S ]*?(?=§)', line)
		if result is not None and symbol != 2:
			help_msg_rtext.append(RText(line).c(RAction.suggest_command, result.group()).h(tr('click_to_fill', result.group())))
			symbol = 1
		else:
			help_msg_rtext.append(line)
			if symbol == 1:
				symbol += 1
	source.reply(help_msg_rtext)


def reload_config(source: Optional[CommandSource]):
	try:
		global sessions
		sessions.clear()
		common.config = common.server_inst.load_config_simple(constants.CONFIG_FILE, target_class=Configure, source_to_reply=source)
	except Exception as e:
		source.get_server().logger.error('Config reload failed ({})'.format(e))


def on_load(server: PluginServerInterface, old_inst):
	global METADATA
	METADATA = server.get_self_metadata()
	common.server_inst = server
	common.action_logger = Logger(server, os.path.join(server.get_data_folder(), constants.LOG_FILE))
	reload_config(None)
	register_stuffs(server)


def register_stuffs(server: PluginServerInterface):
	server.register_command(
		Literal(constants.PREFIX).
		requires(lambda src: src.has_permission(common.config.permission_requirement), lambda: tr('permission_denied')).
		runs(show_help).
		on_error(UnknownArgument, lambda src: src.reply(RText(tr('unknown_command')).h(constants.PREFIX).c(RAction.run_command, constants.PREFIX))).
		then(
			Literal('ls').
			runs(lambda src: list_file(src, None)).
			then(
				Integer('page').
				runs(lambda src, ctx: list_file(src, ctx['page']))
			)
		).
		then(
			Literal('search').
			then(
				QuotableText('keyword').
				runs(lambda src, ctx: search_file(src, ctx['keyword'], None)).
				then(
					Integer('page').
					runs(lambda src, ctx: search_file(src, ctx['keyword'], ctx['page']))
				)
			).
			on_error(UnknownCommand, lambda src: src.reply(tr('command_hint.keyword')))
		).
		then(Literal('pwd').runs(print_current_dir)).
		then(Literal('cd').then(
			QuotableText('path').
			runs(lambda src, ctx: change_dir(src, ctx['path']))
		)).
		then(Literal('delete').then(
			QuotableText('file_name').
			runs(lambda src, ctx: delete_file(src, ctx['file_name']))
		)).
		then(
			Literal('rename').then(
				QuotableText('file_name').then(
					QuotableText('new_name').
					runs(lambda src, ctx: rename_file(src, ctx['file_name'], ctx['new_name']))
				).
				on_error(UnknownCommand, lambda src: src.reply(tr('command_hint.file_name')))
			)
		).
		then(Literal('export').then(
			QuotableText('file_name').
			runs(lambda src, ctx: export_file(src, ctx['file_name']))
		)).
		then(Literal('import').then(
				QuotableText('url').
				runs(lambda src, ctx: import_file(src, ctx['url'], None)).
				then(
					QuotableText('file_name').
					runs(lambda src, ctx: import_file(src, ctx['url'], ctx['file_name']))
				)
			).
			on_error(UnknownCommand, lambda src: src.reply(tr('command_hint.url')))
		).
		then(Literal('reload').runs(reload_config))
	)
	server.register_help_message(constants.PREFIX, METADATA.description, permission=common.config.permission_requirement)
