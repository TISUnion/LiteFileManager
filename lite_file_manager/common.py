from typing import TYPE_CHECKING

from mcdreforged.api.all import *

if TYPE_CHECKING:
	from lite_file_manager.config import Configure
	from lite_file_manager.operation_logger import Logger

server_inst: PluginServerInterface
action_logger: 'Logger'
config: 'Configure'


def tr(translation_key: str, *args, **kwargs) -> RTextMCDRTranslation:
	return server_inst.rtr('{}.{}'.format(server_inst.get_self_metadata().id, translation_key), *args, **kwargs)
