__all__ = [
    'BaseStorage',
    'JSONLocalStorage',
    'S3Storage',
]

import attr
import base64
import collections
import json
import os
from contextlib import contextmanager
from io import BytesIO
from typing import Any, ContextManager, Optional, Type, Union
from ..util.stored import get_base64_digest, get_stored_meta, pickle_dumps_base64, pickle_loads_base64
from .locker import BaseLocker, FileLocker


class BaseStorage:
    def _get_path_by_key(self, key: str) -> str:
        return f'{self.__class__.__name__}_{get_base64_digest(key)}'

    def lock(self, key: str) -> ContextManager[Any]:
        pass

    def save(self, key: str, value: Any) -> None:
        raise NotImplementedError(f'Not implemented method save in {self.__class__.__name__}')

    def load(self, key: str) -> Optional[Any]:
        raise NotImplementedError(f'Not implemented method load in {self.__class__.__name__}')

    def cleanup(self, key: str, lock: Any) -> None:
        pass


@attr.s
class BaseExternalLockerStorage(BaseStorage):
    locker: Optional[BaseLocker] = attr.ib(default=None, kw_only=True)

    @contextmanager
    def lock(self, key: str) -> ContextManager[Any]:
        if self.locker:
            with self.locker(key) as lock:
                yield lock
                return
        yield None

    def cleanup(self, key: str, lock: Any) -> None:
        if self.locker:
            self.locker.cleanup(lock)


@attr.s
class JSONLocalStorage(BaseExternalLockerStorage):
    """Simplest local storage to dump state of a pipeline and restore in case of restart.

    Attributes:
        dirname: Directory to store pipeline's state. By default, "/tmp".
        locker: Optional locker object. By default, FileLocker with the same dirname is used.

    Example:
        Allow pipeline to dump it's state to the local storage.

        >>> pipeline = Pipeline(storage=JSONLocalStorage())
        >>> ...
        >>> await pipeline.run()  # Will load from storage at the start and save after each iteration.
        ...

        Set locker explicitly.

        >>> storage = JSONLocalStorage('/store-data-here', locker=FileLocker('/store-locks-here'))
        ...
    """

    class DefaultNearbyFileLocker:
        pass

    dirname: str = attr.ib(default='/tmp')
    locker: Union[None, DefaultNearbyFileLocker, BaseLocker] = attr.ib(factory=DefaultNearbyFileLocker, kw_only=True)

    def __attrs_post_init__(self) -> None:
        if isinstance(self.locker, self.DefaultNearbyFileLocker):
            self.locker = FileLocker(self.dirname)

    def _get_path_by_key(self, key: str) -> str:
        return os.path.join(self.dirname, super()._get_path_by_key(key))

    def save(self, key: str, value: Any) -> None:
        path = self._get_path_by_key(key)
        with open(path, 'w') as file:
            data = {'key': base64.b64encode(key.encode()).decode(),
                    'value': pickle_dumps_base64(value).decode(),
                    'meta': get_stored_meta()}
            json.dump(data, file, indent=4)

    def load(self, key: str) -> Optional[Any]:
        path = self._get_path_by_key(key)
        try:
            with open(path, 'r') as file:
                content = file.read()
                return pickle_loads_base64(json.loads(content)['value']) if content else None
        except FileNotFoundError:
            return None

    def cleanup(self, key: str, lock: Any) -> None:
        try:
            os.remove(self._get_path_by_key(key))
        except FileNotFoundError:
            pass
        super().cleanup(key, lock)


BucketType = Type


@attr.s
class S3Storage(BaseExternalLockerStorage):
    """Storage that save to AWS S3 using given boto3 client.

    Attributes:
        bucket: Boto3 bucket object.
        locker: Optional locker object. By default, no locker is used.

    Examples:
        Create new instance.

        >>> !pip install boto3
        >>> import boto3
        >>> import os
        >>> session = boto3.Session(
        ...     aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        ...     aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
        ... )
        >>> resource = session.resource('s3', region_name=os.getenv('AWS_DEFAULT_REGION', 'us-east-2'))
        >>> bucket = resource.Bucket('my-bucket')
        >>> storage = S3Storage(bucket)
        ...

        Use with pipelines.

        >>> storage = S3Storage(bucket=bucket, locker=ZooKeeperLocker(kazoo_client, '/lock-dir'))
        >>> pipeline = Pipeline(storage=storage)
        >>> ...
        >>> await pipeline.run()  # Will load from storage at the start and save after each iteration.
        ...
    """
    bucket: BucketType = attr.ib()

    @classmethod
    def _is_not_found_error(cls, exc: Exception) -> bool:
        response = getattr(exc, 'response', None)
        if isinstance(response, collections.Mapping):
            error_info = response.get('Error')
            if error_info and 'Code' in error_info and 'Message' in error_info:
                return error_info['Code'] == '404' and error_info['Message'] == 'Not Found'
        return False

    def save(self, key: str, value: Any) -> None:
        path = self._get_path_by_key(key)
        stream = BytesIO(pickle_dumps_base64(value))
        self.bucket.upload_fileobj(stream, path, ExtraArgs={'Metadata': {'key': key, **get_stored_meta()}})

    def load(self, key: str) -> None:
        path = self._get_path_by_key(key)
        try:
            with BytesIO() as file:
                self.bucket.download_fileobj(path, file)
                file.seek(0)
                content = file.read()
            return pickle_loads_base64(content) if content else None
        except Exception as exc:
            if self._is_not_found_error(exc):
                return None
            raise

    def cleanup(self, key: str, lock: Any) -> None:
        try:
            self.bucket.Object(self._get_path_by_key(key)).delete()
        except Exception:
            pass
        super().cleanup(key, lock)
