import copy
import logging
from pathlib import Path
from typing import Tuple, Dict, Union, Optional, List
import fsspec
import tempfile
import shutil

from runhouse.rns.resource import Resource
from runhouse.rh_config import rns_client, configs
from runhouse.rns.api_utils.resource_access import ResourceAccess

logger = logging.getLogger(__name__)

PROVIDER_FS_LOOKUP = {'aws': 's3',
                      'gcp': 'gcs',
                      'azure': 'abfs',
                      'oracle': 'ocifs',
                      'databricks': 'dbfs',
                      'github': 'github'
                      }


class Folder(Resource):
    RESOURCE_TYPE = 'folder'
    DEFAULT_FS = 'file'

    def __init__(self,
                 name: str = None,
                 url: str = None,
                 fs: str = DEFAULT_FS,
                 save_to: Optional[List[str]] = None,
                 dryrun: bool = True,
                 local_mount: bool = False,
                 data_config: dict = {},
                 **kwargs  # We have this here to ignore extra arguments when calling from from_config
                 ):
        """
        TODO [DG] Update
        Include loud warning that relative paths are relative to the git root / working directory!
        Args:
            name ():
            parent (): string path to parent folder, or
            data_source (): FSSpec protocol, e.g. 's3', 'gcs'. See/run `fsspec.available_protocols()`.
                Default is "file", the local filesystem to wherever the blob is created.
            data_config ():
            local_path ():
            serializer ():
        """
        super().__init__(name=name,
                         dryrun=dryrun,
                         save_to=save_to)

        # TODO if fs='github', check if we need to extract the data_config and url properly, e.g.
        # rh.Folder(url='https://github.com/pytorch/pytorch', fs='github')
        # should be resolved as:
        # rh.Folder(url='/', fs='github', data_config={'org': 'pytorch', 'repo': 'pytorch'})

        # If the url is not absolute, assume that it's relative to the working directory
        self._url = url if (url is None or Path(url).expanduser().is_absolute()) \
            else str(Path(rns_client.locate_working_dir()) / url)
        self.fs = fs

        # TODO maybe we can provide reasonable defaults or config options for cloud blob storage, etc.
        if self.fs not in [None, self.DEFAULT_FS] and self.url is None:
            raise ValueError('You must provide a url to use a non-local provider for folder storage.')

        self.local_mount = local_mount
        self._local_mount_path = None
        if local_mount:
            self.mount(tmp=True)
        self.data_config = data_config or {}
        self.virtual_children = []

        if self._name is None:
            if self.url is None:
                # Create anonymous folder
                self._tempdir = tempfile.TemporaryDirectory()
                self.url = self._tempdir.name
                Path(self.url).mkdir(parents=True, exist_ok=True)
                self.fs = 'file'
            elif self.fs == 'file':
                self._name = Path(self.url).stem
            # If there's a url, but fs != 'file', this is an anonymous Folder with a remote fs (e.g. "github").

    @staticmethod
    def from_config(config: dict, dryrun=True):
        """ Load config values into the object. """
        return Folder(**config, dryrun=dryrun)

    @property
    def url(self):
        if self._url is not None:
            return str(Path(self._url).expanduser())
        else:
            return None

    @url.setter
    def url(self, url):
        self._url = url
        self._local_mount_path = None

    @property
    def local_path(self):
        if self.is_local():
            return self._local_mount_path or str(Path(self.url).expanduser())
        else:
            return None

    def is_writable(self):
        fsspec_fs = fsspec.filesystem(self.fs)

        # If the filesystem hasn't overridden mkdirs, it's a no-op and the filesystem is probably readonly
        # (e.g. https://filesystem-spec.readthedocs.io/en/latest/_modules/fsspec/implementations/github.html).
        # In that case, we should just create a new folder in the default
        # location and add it as a child to the parent folder.
        return fsspec_fs.__class__.mkdirs == fsspec.AbstractFileSystem.mkdirs

    def mv(self, fs, url=None, data_config=None) -> None:
        """Move the folder to a new filesystem.

        Args:
            fs (str): file system.
            url (:obj:`str`, optional): fsspec URL.
            data_config(:obj:`dict`, optional): Config to move.
        """
        if url is None:
            url = 'rh/' + self.rns_address
        with fsspec.open(self.fsspec_url, **self.data_config) as src:
            with fsspec.open(f'{fs}://{url}', **data_config) as dest:
                # NOTE For packages, maybe use the `ignore` param here to only copy python files.
                new_url = shutil.move(src, dest)
        self.url = new_url
        self.fs = fs
        self.data_config = data_config or {}

    def cp(self, fs, url=None, data_config=None):
        """ Copy the folder to a new filesystem, and return a new Folder object pointing to the new location. """
        if url is None:
            url = 'runhouse/' + self.rns_address[1:].replace('/', '_') + f'.{self.RESOURCE_TYPE}'

        logging.info(f'Copying folder from {self.fsspec_url} to {fs}://{url}')

        # TODO will this work if the base bucket doesn't exist yet?
        if self.is_local():
            fsspec.filesystem(fs).put(self.url, f'{fs}://{url}', recursive=True)
        else:
            # TODO this is really really slow, maybe use skyplane, as follows:
            # src_url = f'local://{self.url}' if self.is_local() else self.fsspec_url
            # subprocess.run(['skyplane', 'sync', src_url, f'{fs}://{url}'])

            # FYI: from https://github.com/fsspec/filesystem_spec/issues/909
            # TODO [DG]: Copy chunks https://github.com/fsspec/filesystem_spec/issues/909#issuecomment-1204212507
            src = fsspec.get_mapper(self.fsspec_url, create=False, **self.data_config)
            dest = fsspec.get_mapper(f'{fs}://{url}', create=True, **data_config)
            # dest.fs.mkdir(dest.root, create_parents=True)
            import tqdm
            for k in tqdm.tqdm(src):
                # NOTE For packages, maybe use the `ignore` param here to only copy python files.
                dest[k] = src[k]
                # dst.write(src.read())
        new_folder = copy.deepcopy(self)
        new_folder.url = url
        new_folder.fs = fs
        new_folder.data_config = data_config or {}
        return new_folder

    def mount(self, url=None, tmp=False, data_config={}) -> str:
        """ Mount the folder locally. """
        # TODO check that fusepy and FUSE are installed
        if tmp:
            self._local_mount_path = tempfile.mkdtemp()
        else:
            self._local_mount_path = url
        remote_fs = fsspec.filesystem(self.fs, **data_config)
        fsspec.fuse.run(fs=remote_fs, path=self.url, mount_point=self._local_mount_path)
        return self._local_mount_path

    def to_cluster(self, cluster, url=None, mount=False, return_dest_folder=False):
        """ Copy the folder onto a cluster. """
        if not cluster.address:
            raise ValueError('Cluster must be started before copying data to it.')
        # Create tmp_mount if needed
        if not self.is_local() and mount:
            self.mount(tmp=True)
        src_url = self.local_path + '/'  # Need to add slash for rsync to copy the contents of the folder
        dest_url = url or f'~/{Path(self.url).stem}'
        cluster.rsync(source=src_url, dest=dest_url, up=True)
        dest_folder = copy.deepcopy(self)
        dest_folder.url = dest_url
        dest_folder.fs = 'file'
        if return_dest_folder:
            return dest_folder
        return dest_folder.from_cluster(cluster)

    def sync_from_cluster(self, cluster, url):
        """ Efficiently rsync down a folder from a cluster, into the url of the current Folder object. """
        if not cluster.address:
            raise ValueError('Cluster must be started before copying data to it.')
        # TODO support fsspec urls (e.g. nonlocal fs's)?
        cluster.rsync(source=self.url, dest=url, up=False)

    def from_cluster(self, cluster):
        """ Create a remote folder from a url on a cluster. This will create a virtual link into the
        cluster's filesystem. If you want to create a local copy or mount of the folder, use
        `Folder(url=<local_url>).sync_from_cluster(<cluster>, <url>)` or
        `Folder('url').from_cluster(<cluster>).mount(<local_url>)`. """
        if not cluster.address:
            raise ValueError('Cluster must be started before copying data from it.')
        creds = cluster.ssh_creds()
        data_config = {'host': cluster.address,
                       'ssh_creds': {'username': creds['ssh_user'],
                                     'pkey': creds['ssh_private_key']}
                       }
        new_folder = copy.deepcopy(self)
        new_folder.fs = 'sftp'
        new_folder.data_config = data_config
        return new_folder

    def is_local(self):
        return (self.url is not None and Path(self.url).exists()) or self._local_mount_path

    def share(self,
              users: list,
              access_type: Union[ResourceAccess, str] = ResourceAccess.read,
              snapshot: bool = True,
              snapshot_fs: str = None,
              snapshot_compression: str = None,
              snapshot_url: str = None,
              ) -> Tuple[Dict[str, ResourceAccess], Dict[str, ResourceAccess]]:
        """Granting access to the resource for list of users (via their emails). If a user has a Runhouse account they
        will receive an email notifying them of their new access. If the user does not have a Runhouse account they will
        also receive instructions on creating one, after which they will be able to have access to the Resource.
        Note: You can only grant resource access to other users if you have Write / Read privileges for the Resource"""
        if self.is_local() and snapshot:
            # raise ValueError('Cannot share a local resource.')
            fs = snapshot_fs or PROVIDER_FS_LOOKUP[configs.get('default_provider')]
            if fs not in fsspec.available_protocols():
                raise ValueError(f'Invalid mount_fs: {snapshot_fs}. Must be one of {fsspec.available_protocols()}')
            if snapshot_compression not in fsspec.available_compressions():
                raise ValueError(f'Invalid mount_compression: {snapshot_compression}. Must be one of '
                                 f'{fsspec.available_compressions()}')
            data_config = {'compression': snapshot_compression} if snapshot_compression else {}
            snapshot_folder = self.cp(fs=fs, url=snapshot_url, data_config=data_config)

            # Is this a bad idea? Better to store the snapshot config as the source of truth than the local url
            snapshot_folder.save(name=self.rns_address, save_to=['rns'])

            return snapshot_folder.share(users=users, access_type=access_type, snapshot=False)

        # TODO just call super().share
        if isinstance(access_type, str):
            access_type = ResourceAccess(access_type)
        if not rns_client.exists(self.rns_address, load_from=['rns']):
            self.save(save_to=['rns'])
        added_users, new_users = rns_client.grant_resource_access(resource_name=self.name,
                                                                  user_emails=users,
                                                                  access_type=access_type)
        return added_users, new_users

    @property
    def config_for_rns(self):
        config = super().config_for_rns
        config_attrs = ['local_mount', 'data_config']
        self.save_attrs_to_config(config, config_attrs)

        if not self.fs == self.DEFAULT_FS:
            config['url'] = self.url
            config['fs'] = self.fs
        elif self.url is not None:
            config['url'] = self._url_relative_to_rh_workdir(self.url)
        return config

    @staticmethod
    def _url_relative_to_rh_workdir(url):
        rh_workdir = Path(rns_client.locate_working_dir())
        try:
            return str(Path(url).relative_to(rh_workdir))
        except ValueError:
            return url

    @property
    def fsspec_url(self):
        """Generate the FSSpec URL using the data_source and data_url"""
        return f'{self.fs}://{self.url}'

    def ls(self, full_paths: bool = False, resource_type: str = None):
        """List the resources in the *RNS* folder.

        Args:
            full_paths (bool): If True, return the full RNS path for each resource. If False, return the
                resource name only.
            resource_type (str): If provided, only return resources of the specified type.
        """
        # TODO filter by type
        # TODO allow '*' wildcard for listing all resources (and maybe other wildcard things)
        fs = fsspec.filesystem(self.fs, **self.data_config)
        # TODO don't exclude physical files that are not resources?
        physical_children = [path for path in fs.ls(path=self.url)
                             if (Path(path) / 'config.json').exists()] \
            if self.url else []

        virtual_children = set()
        if self.name:  # Anonymous folders can't have non-local rns children
            for (rns_path, url) in rns_client.rns_base_folders.items():
                if rns_path.startswith(self.rns_address) and rns_path != self.rns_address:
                    child_name = Path(rns_path[len(self.rns_address):]).name
                    virtual_children.add(self.rns_address + '/' + child_name)

        if full_paths:
            return [self.rns_address + '/' + Path(path).stem for path in physical_children] + list(virtual_children)
        else:
            return [Path(path).stem for path in physical_children + list(virtual_children)]

    @property
    def rns_address(self):
        """ Traverse up the filesystem until reaching one of the directories in rns_base_folders,
        then compute the relative path to that.

        Maybe later, account for folders along the path with a different RNS name."""

        if self.name is None:  # Anonymous folders have no rns address
            return None

        # Only should be necessary when a new base folder is being added (therefore isn't in rns_base_folders yet)
        if self._rns_folder:
            return str(Path(self._rns_folder) / self.name)

        if self.url in rns_client.rns_base_folders.values():
            if self._rns_folder:
                return self._rns_folder + '/' + self.name
            else:
                return rns_client.default_folder + '/' + self.name

        segment = Path(self.url)
        while not str(segment) in rns_client.rns_base_folders.values() and \
                not segment == Path.home() and \
                not segment == segment.parent:
            segment = segment.parent

        if segment == Path.home() or segment == segment.parent:  # TODO throw an error instead?
            return rns_client.default_folder + '/' + self.name
        else:
            base_folder = Folder(url=str(segment),
                                 dryrun=True)
            base_folder_path = base_folder.rns_address
            relative_path = str(Path(self.url).relative_to(base_folder.url))
            return base_folder_path + '/' + relative_path

    def contains(self, name_or_path) -> bool:
        url, fs = self.locate(name_or_path)
        return url is not None

    def locate(self, name_or_path) -> (str, str):
        """ Locate the local url of a Folder given an rns path.

        Keep in mind we're using both _rns_ path and physical path logic below. Be careful!
        """

        # If the path is already given relative to the current folder:
        if (Path(self.url) / name_or_path).exists():
            return str(Path(self.url) / name_or_path), self.fs

        # If name or path uses ~/ or ./, need to resolve with folder url
        abs_path = rns_client.resolve_rns_path(name_or_path)
        rns_path = self.rns_address

        # If this folder is anonymous, it has no rns contents
        if rns_path is None:
            return None, None

        if abs_path == rns_path:
            return self.url, self.fs
        try:
            child_url = Path(self.url) / Path(abs_path).relative_to(rns_path)
            if child_url.exists():
                return str(child_url), self.fs
        except ValueError:
            pass

        # Last resort, recursively search inside sub-folders and children.

        segments = abs_path.lstrip('/').split('/')
        if len(segments) == 1:
            return None, None  # If only a single element, would have been found in ls above.

        # Look for lowest folder in the path that exists in filesystem, and recurse from that folder
        greatest_common_folder = Path(self.url)
        i = 0
        for i, seg in enumerate(segments):
            if not (greatest_common_folder / seg).exists():
                break
            greatest_common_folder = greatest_common_folder / seg
        if not str(greatest_common_folder) == self.url:
            return Folder(url=str(greatest_common_folder),
                          fs=self.fs,
                          dryrun=True).locate('/'.join(segments[i + 1:]))

        if name_or_path in self.virtual_children:
            child = [r for r in self.virtual_children if r.name == name_or_path][0]
            return child.url, child.fs

        segments = abs_path.lstrip('/').split('/')
        # If the child has a different filesystem, this will take over the search from there.
        if segments[0] in self.virtual_children:
            child = [r for r in self.virtual_children if r.name == segments[0]][0]
            return rns_client.locate('/'.join(segments[1:]))

        return None, None

    def get(self, name):
        """ Returns an fsspec file, which must be used as a content manager to be opened!
        e.g. with my_folder.get('obj_name') as my_file:
                pickle.load(my_file)
        """
        return fsspec.open(self.fsspec_url + '/' + name, mode='rb', **self.data_config)

    def get_all(self):
        # TODO we're not closing these, do we need to extract file-like objects so we can close them?
        return fsspec.open_files(self.fsspec_url, mode='rb', **self.data_config)

    def put(self, contents, overwrite=False):
        """
            files: Either 1) A dict with keys being the file names and values being the
                file-like objects to write, 2) a Resource, or 3) a list of Resources.
        """
        # Handle lists of resources just for convenience
        if isinstance(contents, list):
            for resource in contents:
                self.put(resource, overwrite=overwrite)
            return

        if isinstance(contents, Folder):
            if self.is_writable():
                if contents.url is None:  # Should only be the case when Folder is created
                    contents.url = self.url + '/' + contents.name
                    contents.fs = self.fs
                    # The parent can be anonymous, e.g. the 'rh' folder.
                    # TODO not sure if this should be allowed - if parent folder has no rns address, why would child
                    # just be put into the default rns folder?
                    # TODO If the base is named later, figure out what to do with the contents (rename, resave, etc.).
                    if self.rns_address is None:
                        contents.rns_path = rns_client.default_folder + '/' + contents.name
                        rns_client.rns_base_folders.update({contents.rns_address: contents.url})
                    # We don't need to call .save here to write down because it will be called at the end of the
                    # folder or resource constructor
                else:
                    if contents.name is None:  # Anonymous resource
                        i = 1
                        new_name = contents.RESOURCE_TYPE + str(i)
                        fsspec_fs = fsspec.filesystem(self.fs)
                        # Resolve naming conflicts if necessary
                        while rns_client.exists(self.url + '/' + new_name):
                            i += 1
                            new_name = contents.RESOURCE_TYPE + str(i)
                    else:
                        new_name = contents.name

                    # NOTE For intercloud transfer, we should use Skyplane
                    with fsspec.open(self.fsspec_url + '/' + new_name, **self.data_config) as dest:
                        with fsspec.open(contents.fsspec_url, **contents.data_config) as src:
                            # NOTE For packages, maybe use the `ignore` param here to only copy python files.
                            new_url = shutil.move(src, dest)

            # TODO put children into directory
            else:
                # TODO check for naming collisions
                self.virtual_children += [contents]
                # TODO if contents is named, put it into rh_directory and set explicit rns_path
            return

        if not isinstance(contents, dict):
            raise TypeError('`files` argument to `.put` must be Resource, list of Resources, or dict mapping '
                            'filenames to file-like-objects')

        if overwrite is False:
            folder_contents = self.ls()
            intersection = set(folder_contents).intersection(set(contents.keys()))
            if intersection != set():
                raise FileExistsError(f'File(s) {intersection} already exist(s) at url'
                                      f'{self.url}, cannot save them without overwriting.')
        # TODO figure out default behavior for not overwriting but still saving
        # if not overwrite:
        #     time = datetime.today().strftime('%Y-%m-%d_%H:%M:%S')
        #     self.data_url = self.data_url + time or time
        filenames = list(contents.keys())
        fss_files = fsspec.open_files(self.fsspec_url + '/*',
                                      mode='wb',
                                      **self.data_config,
                                      num=len(contents),
                                      name_function=filenames.__getitem__)
        for (fss_file, raw_file) in zip(fss_files, contents.values()):
            with fss_file as f:
                f.write(raw_file)


def folder(name=None,
           url=None,
           fs='file',
           save_to=None,
           load_from=None,
           dryrun=False,
           local_mount: bool = False,
           data_config: dict = {},
           ):
    """ Returns a folder object, which can be used to interact with the folder at the given url.
    If the folder does not exist, it will be created if `create` is True.
    """
    config = rns_client.load_config(name, load_from=load_from)
    config['name'] = name or config.get('rns_address', None) or config.get('name')
    config['url'] = url or config.get('url')
    config['fs'] = fs or config.get('fs')
    config['local_mount'] = local_mount or config.get('local_mount')
    config['data_config'] = data_config or config.get('data_config')
    config['save_to'] = save_to

    new_folder = Folder.from_config(config, dryrun=dryrun)

    if new_folder.name:
        new_folder.save()

    return new_folder
