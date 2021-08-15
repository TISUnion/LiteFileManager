from typing import Dict

from mcdreforged.api.utils.serializer import Serializable

from lite_file_manager import constants


class DirectoryEntry(Serializable):
	path: str
	permission: Dict[str, int]


class Configure(Serializable):
	permission_requirement: int = 2
	max_import_size: int = 10 * 2 ** 20  # 10MB
	file_per_page: int = 10
	directories: Dict[str, DirectoryEntry] = {
		'structures': DirectoryEntry(
			path='./server/world/generated/minecraft/structures',
			permission={
				constants.OpType.read: 2,
				constants.OpType.write: 3
			}
		)
	}
