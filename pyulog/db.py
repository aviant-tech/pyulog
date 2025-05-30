'''
Module containing the DatabaseULog class.
'''

import sqlite3
import hashlib
import contextlib
import numpy as np
from pyulog import ULog

# pylint: disable=too-many-instance-attributes
class DatabaseULog(ULog):
    '''
    This class can be used in place of a ULog, except that it reads from and
    writes to a sqlite3 database instead of a file.

    The first time you see a ulog file, instantiate a DatabaseULog directly
    from a ulog file, and then call save() to write it to the database. Later
    it can be accessed by providing the primary_key, upon which this class
    loads all needed fields from the database. If you don't have the
    primary_key value available, but you have the .ulg file and know it is in
    the database, you can retrieve the hash with DatabaseULog.calc_sha256sum
    and DatabaseULog.primary_key_from_sha256sum.

    This class is currently designed to be write-once only, so you cannot
    update existing database entries. To do so, first call delete() and then
    save() again.

    A weakness of the implementation is that there are some silently failing
    states if you don't call save() immediately after instantiating from a ULog
    object. The requirement of explicit save() is to prevent unexpected, sudden
    side effects, which is considered worse than the current solution.

    Example usage:
    > from pyulog.db import DatabaseULog
    >
    > db_handle = DatabaseULog.get_db_handle('dbulog.sqlite3')
    > dbulog = DatabaseULog(db_handle, log_file='example.ulg') # Slow
    > dbulog.save()
    > pk = dbulog.primary_key()
    > # [...]
    > dbulog = DatabaseULog(db_handle, primary_key=pk) # Fast

    SCHEMA_VERSION specifies which version of the database schema (as read from
    "PRAGMA user_version") this file is supposed to match. This is done to
    ensure consistency between the database operations and the state of the
    database. If SCHEMA_VERSION is higher than what is found in the database,
    then it means that the datbaase is has not been updated, and the
    contsructor will throw an exception. See the documentation of
    "ulog_migratedb" for more information.
    '''
    SCHEMA_VERSION = 5

    @staticmethod
    def get_db_handle(db_path):
        '''
        Generate a database handle that can be used in subsequent database access.
        '''
        def db_handle():
            con = sqlite3.connect(
                db_path,
                detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES
            )
            # The next line is necessary for sqlite3 to actually respect
            # FOREIGN KEY constraints and ON DELETE CASCADE.
            con.execute('PRAGMA foreign_keys=on')
            return con
        return db_handle

    @staticmethod
    def exists_in_db(db_handle, primary_key):
        '''
        Check whether an ULog row with Id=primary_key exists in the database
        accessed with db_handle.
        '''
        with db_handle() as con:
            cur = con.cursor()

            cur.execute('''
                SELECT COUNT(*)
                FROM ULog
                WHERE Id = ?
                ''', (primary_key,))
            count, = cur.fetchone()
            cur.close()

            return count == 1

    @staticmethod
    def primary_key_from_sha256sum(db_handle, sha256sum):
        '''
        Search the database for a row with the given SHA256 digest and returns
        the corresponding primary key. Returns None if not found.
        '''
        primary_key = None
        with db_handle() as con:
            cur = con.cursor()
            cur.execute('''
                SELECT Id
                FROM ULog
                WHERE SHA256Sum = ?
                ''', (sha256sum,))
            row = cur.fetchone()
            if row:
                primary_key = row[0]
            cur.close()
        return primary_key

    @staticmethod
    def calc_sha256sum(log_file):
        '''
        Compute the SHA256 digest of a file, specified as a file or a valid file path.
        '''
        if log_file is None:
            return None
        if isinstance(log_file, str):
            file_context = open(log_file, 'rb') # pylint: disable=consider-using-with
        elif log_file.closed:
            file_context = open(log_file.name, 'rb')
        else:
            file_context = contextlib.nullcontext(log_file)

        file_hash = hashlib.sha256()
        with file_context as open_file:
            while True:
                block = open_file.read(4096)
                file_hash.update(block)
                if block == b'':
                    break
        return file_hash.hexdigest()


    def __init__(self, db_handle, primary_key=None, log_file=None, lazy=True, **kwargs):
        '''
        You always need the database handle (which can be generated with
        DatabaseULog.get_db_handle), but there are two options for the
        parameters "primary_key" and "log_file":
        - For storing a new log in the database, supply the corresponding
          log_file parameter, but leave the primary_key field at None.
        - For reading an existing log from the database, supply the desired
          primary_key parameter, but leave the log_file parameter at None. If
          you don't know the primary_key, you can try finding it with
          DatabaseULog.primary_key_from_sha256sum.
        You cannot supply both of these parameters.

        Furthermore, the "lazy" parameter specifies whether all data fields
        should be read on-demand when get_dataset is called, or if they should
        all be read and populated into data_list when the object is
        instantiated.

        The constructor also checks that SCHEMA_VERSION matches the "PRAGMA
        user_version" found in the database, see the documentation of "PRAGMA
        user_version" for more information.
        '''

        with db_handle() as con:
            cur = con.cursor()
            cur.execute('PRAGMA user_version')
            (db_version,) = cur.fetchone()
            if db_version < DatabaseULog.SCHEMA_VERSION:
                raise ValueError('Database version %d < schema version %d, migration needed.' % (
                    db_version,
                    DatabaseULog.SCHEMA_VERSION
            ))

        if log_file is not None and primary_key is not None:
            raise ValueError('You cannot provide both primary_key and log_file.')
        if log_file is None and primary_key is None:
            raise ValueError('You must provide either a primary_key or log_file.')


        self._pk = primary_key
        self._db = db_handle
        self._lazy_loaded = lazy
        if log_file is not None:
            self._sha256sum = DatabaseULog.calc_sha256sum(log_file)

        super().__init__(log_file, **kwargs)
        if primary_key is not None:
            self.load(lazy=lazy)

    def __eq__(self, other):
        """
        If the other object is a normal ULog, then we just want to compare ULog
        data, not DatabaseULog specific fields, because we want to compare
        theULog file contents.
        """
        if type(other) is ULog:  # pylint: disable=unidiomatic-typecheck
            return other.__eq__(self)
        return super().__eq__(other)

    def write_ulog(self, log_file):
        if self._lazy_loaded:
            raise ValueError('Cannot write after lazy load because it has no datasets.')
        super().write_ulog(log_file)

    @property
    def primary_key(self):
        '''The primary key of the ulog, pointing to the correct "ULog" row in the database.'''
        return self._pk

    @property
    def sha256sum(self):
        '''The computed SHA256 digest of the file, stored for later use.'''
        return self._sha256sum

    # pylint: disable=too-many-locals,too-many-branches
    def load(self, lazy=True):
        '''
        Load all necessary data from the database, possibly except for the data series
        themselves, which can cost unnecessary resources.

        If lazy=True, then the data series will be left unread, deferred until
        get_dataset is called. If however lazy=False, then all data series will
        be read at once.

        Even if the log was originally saved with append_json=True, this
        function will always use the faster BLOB column for retrieval.
        '''
        if not DatabaseULog.exists_in_db(self._db, self._pk):
            raise KeyError(f'No ULog in database with Id={self._pk}')

        with self._db() as con:
            cur = con.cursor()

            # ULog metadata
            cur.execute('''
                SELECT FileVersion,
                       StartTimestamp,
                       LastTimestamp,
                       CompatFlags,
                       IncompatFlags,
                       SyncCount,
                       HasSync,
                       SHA256Sum
                FROM ULog
                WHERE Id = ?
                ''', (self._pk,))
            ulog_result = cur.fetchone()
            self._file_version = ulog_result[0]
            self._start_timestamp = ulog_result[1]
            self._last_timestamp = ulog_result[2]
            self._compat_flags = [ord(c) for c in ulog_result[3]]
            self._incompat_flags = [ord(c) for c in ulog_result[4]]
            self._sync_seq_cnt = ulog_result[5]
            self._has_sync = ulog_result[6]
            self._sha256sum = ulog_result[7]

            # appended_offsets
            cur.execute('''
                SELECT Offset
                FROM ULogAppendedOffsets
                WHERE ULogId = ?
                ORDER BY SeriesIndex
                ''', (self._pk,))
            offsets_result = cur.fetchall()
            for offset, in offsets_result:
                self._appended_offsets.append(offset)

            # data_list
            self._data_list = []
            cur.execute('''
                SELECT DatasetName, MultiId
                FROM ULogDataset
                WHERE ULogId = ?
                ORDER BY DatasetName, MultiId
                ''', (self._pk,))
            dataset_results = cur.fetchall()
            for dataset_name, multi_id in dataset_results:
                dataset = self.get_dataset(dataset_name,
                                           multi_instance=multi_id,
                                           lazy=lazy,
                                           db_cursor=cur,
                                           caching=False)
                self._data_list.append(dataset)

            # dropouts
            cur.execute('''
                SELECT Timestamp, Duration
                FROM ULogMessageDropout
                WHERE ULogId = ?
                ''', (self._pk,))
            for timestamp, duration in cur.fetchall():
                self._dropouts.append(
                    DatabaseULog.DatabaseMessageDropout(
                        timestamp=timestamp,
                        duration=duration,
                    )
                )

            # logged_messages
            cur.execute('''
                SELECT LogLevel, Timestamp, Message
                FROM ULogMessageLogging
                WHERE ULogId = ?
                ''', (self._pk,))
            for log_level, timestamp, message in cur.fetchall():
                self._logged_messages.append(
                    DatabaseULog.DatabaseMessageLogging(
                        log_level=log_level,
                        timestamp=timestamp,
                        message=message,
                    )
                )

            # logged_messages_tagged
            cur.execute('''
                SELECT LogLevel, Tag, Timestamp, Message
                FROM ULogMessageLoggingTagged
                WHERE ULogId = ?
                ''', (self._pk,))
            for log_level, tag, timestamp, message in cur.fetchall():
                if tag not in self._logged_messages_tagged:
                    self._logged_messages_tagged[tag] = []
                self._logged_messages_tagged[tag].append(
                    DatabaseULog.DatabaseMessageLoggingTagged(
                        log_level=log_level,
                        tag=tag,
                        timestamp=timestamp,
                        message=message,
                    )
                )

            # message_formats
            cur.execute('''
                SELECT
                    msg.Name,
                    field.FieldType,
                    field.ArraySize,
                    field.Name
                FROM ULogMessageFormat msg JOIN ULogMessageFormatField field
                    ON field.MessageId = msg.Id
                WHERE ULogId = ?
                ''', (self._pk,))
            for row in cur.fetchall():
                msg_name = row[0]
                field_data = row[1:]
                if msg_name in self._message_formats:
                    self._message_formats[msg_name].fields.append(field_data)
                else:
                    self._message_formats[msg_name] = DatabaseULog.DatabaseMessageFormat(
                        name=msg_name,
                        fields=[field_data]
                    )

            # msg_info_dict
            cur.execute('''
                SELECT Key, Typename, Value
                FROM ULogMessageInfo
                WHERE ULogId = ?
                ''', (self._pk,))
            for key, typename, value in cur.fetchall():
                self._msg_info_dict[key] = value
                self._msg_info_dict_types[key] = typename

            # msg_info_multiple_dict
            cur.execute('''
                SELECT Id, Key, Typename
                FROM ULogMessageInfoMultiple
                WHERE ULogId = ?
                ''', (self._pk,))
            for message_id, key, typename in cur.fetchall():
                self._msg_info_multiple_dict[key] = []
                self._msg_info_multiple_dict_types[key] = typename
                cur.execute('''
                    SELECT Id
                    FROM ULogMessageInfoMultipleList
                    WHERE MessageId = ?
                    ORDER BY SeriesIndex
                    ''', (message_id,))
                for (list_id,) in cur.fetchall():
                    cur.execute('''
                        SELECT Value
                        FROM ULogMessageInfoMultipleListElement
                        WHERE ListId = ?
                        ORDER BY SeriesIndex
                        ''', (list_id,))
                    self._msg_info_multiple_dict[key].append([value for (value,) in cur.fetchall()])

            # initial_parameters
            cur.execute('''
                SELECT Key, Value
                FROM ULogInitialParameter
                WHERE ULogId = ?
                ''', (self._pk,))
            for key, value in cur.fetchall():
                self._initial_parameters[key] = value

            # _default_parameters
            cur.execute('''
                SELECT DefaultType, Key, Value
                FROM ULogDefaultParameter
                WHERE ULogId = ?
                ''', (self._pk,))
            for default_type, key, value in cur.fetchall():
                if default_type not in self._default_parameters:
                    self._default_parameters[default_type] = {}
                self._default_parameters[default_type][key] = value

            # changed_parameters
            cur.execute('''
                SELECT Timestamp, Key, Value
                FROM ULogChangedParameter
                WHERE ULogId = ?
                ''', (self._pk,))
            for timestamp, key, value in cur.fetchall():
                self._changed_parameters.append((timestamp, key, value))

            cur.close()
        self._lazy_loaded = lazy

    def get_dataset(self, name, multi_instance=0, lazy=False, db_cursor=None, caching=True):
        '''
        Access a specific dataset and its data series from the database.

        The "lazy" argument specifies whether only dataset metadata should be
        retrieved from the database, or if the data series arrays should be
        retrieved too.

        The optional "db_cursor" argument can be used to avoid re-opening the
        database connection each time get_dataset is called.

        Since we don't expect the data to change often, we will normally use
        self._data_list as a cache, and check there before reading from the
        database. However, if caching=False, then we will always read anew
        from the database.
        '''

        if db_cursor is None:
            db_context = self._db()
            cur = db_context.cursor()
        else:
            db_context = contextlib.nullcontext()
            cur = db_cursor

        existing_dataset = None
        for dataset in self._data_list:
            if dataset.name == name and dataset.multi_id == multi_instance:
                existing_dataset = dataset
                break

        if (caching
                and existing_dataset is not None
                and (lazy or existing_dataset.data)):
            return existing_dataset

        with db_context:
            cur.execute('''
                SELECT Id, TimestampIndex, MessageId
                FROM ULogDataset
                WHERE DatasetName = ? AND MultiId = ? AND ULogId = ?
                ''', (name, multi_instance, self._pk))
            dataset_result = cur.fetchone()
            if dataset_result is None:
                raise KeyError(f'Dataset with name {name} and multi id {multi_instance} not found.')
            dataset_id, timestamp_idx, msg_id = dataset_result
            fields = []
            data = {}
            if not lazy:
                cur.execute('''
                    SELECT TopicName, DataType, ValueArray
                    FROM ULogField
                    WHERE DatasetId = ?
                    ''', (dataset_id,))
                field_results = cur.fetchall()
                for field_name, data_type, value_bytes in field_results:
                    fields.append(DatabaseULog._FieldData(field_name=field_name,type_str=data_type))
                    dtype = DatabaseULog._UNPACK_TYPES[data_type][2]
                    data[field_name] = np.frombuffer(value_bytes, dtype=dtype)

        # If caching=True but there is no existing dataset we could append a
        # new one to self._data_list, but that could be considered a
        # non-obvious side effect.
        if caching and existing_dataset is not None:
            existing_dataset.msg_id = msg_id
            existing_dataset.timestamp_idx = timestamp_idx
            existing_dataset.field_data = fields
            existing_dataset.data = data
            dataset = existing_dataset
        else:
            dataset = DatabaseULog.DatabaseData(
                name=name,
                multi_id=multi_instance,
                msg_id=msg_id,
                timestamp_idx=timestamp_idx,
                field_data=fields,
                data=data,
            )
        return dataset

    def save(self, append_json=False):
        '''
        Save the DatabaseULog to the database. Throws a KeyError if the primary
        key is already in the database.

        The datasets are stored in a sqlite BLOB for fast retrieval, but if
        append_json=True, then datasets are additionally stored in a JSON
        field. This allows them to be directly queried using the sqlite
        function json_each, but increases the writing time and database size.
        '''

        if self._pk is not None:
            raise KeyError('Cannot save logs that are already in the database')

        pk_from_hash = DatabaseULog.primary_key_from_sha256sum(self._db, self._sha256sum)
        if pk_from_hash is not None:
            self._pk = pk_from_hash
            raise KeyError(f'Hash {self._sha256sum} already in database with Id={pk_from_hash}')

        with self._db() as con:
            cur = con.cursor()

            # ULog metadata
            cur.execute('''
                INSERT INTO ULog
                (FileVersion, StartTimestamp, LastTimestamp, CompatFlags, IncompatFlags, SyncCount, HasSync, SHA256Sum)
                VALUES
                (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                    self._file_version,
                    self._start_timestamp,
                    self._last_timestamp,
                    ''.join([chr(n) for n in self._compat_flags]),
                    ''.join([chr(n) for n in self._incompat_flags]),
                    self._sync_seq_cnt,
                    self._has_sync,
                    self._sha256sum,
                )
            )
            self._pk = cur.lastrowid

            # appended_offsets
            cur.executemany('''
                INSERT INTO ULogAppendedOffsets
                (SeriesIndex, Offset, ULogId)
                VALUES
                (?, ?, ?)
                ''', [(
                    list_index,
                    offset,
                    self._pk,
                ) for list_index, offset in enumerate(self._appended_offsets)])

            # data_list
            for dataset in self.data_list:
                cur.execute('''
                    INSERT INTO ULogDataSet
                    (DatasetName, MultiId, MessageId, TimestampIndex, ULogId)
                    VALUES
                    (?, ?, ?, ?, ?)
                    ''', (
                        dataset.name,
                        dataset.multi_id,
                        dataset.msg_id,
                        dataset.timestamp_idx,
                        self._pk,
                    )
                )
                dataset_id = cur.lastrowid
                for field in dataset.field_data:
                    values = dataset.data[field.field_name]
                    values_bytes = values.tobytes()
                    if append_json:
                        # Precision is only good enough up to a few decimals,
                        # depending on the default float formatter. The
                        # function np.array2string was tested, but was slower.
                        # Also note that doing the slow json.dumps is also
                        # unnecessary since we know that the object to be
                        # formatted is an array.
                        timestamp_list = [str(s) for s in dataset.data['timestamp'].tolist()]
                        values_json = str(
                            dict(zip(timestamp_list, values.tolist()))
                        ).replace('nan', 'null').replace('inf', 'null').replace("'", '"')
                        json_placeholder = 'json(?)'  # Saves some space
                    else:
                        values_json = None
                        json_placeholder = '?'

                    cur.execute(f'''
                        INSERT INTO ULogField
                        (TopicName, DataType, ValueArray, ValueJson, DatasetId)
                        VALUES
                        (?, ?, ?, {json_placeholder}, ?)
                        ''', (
                            field.field_name,
                            field.type_str,
                            values_bytes,
                            values_json,
                            dataset_id,
                        )
                    )

            # dropouts
            cur.executemany('''
                INSERT INTO ULogMessageDropout
                (Timestamp, Duration, ULogId)
                VALUES
                (?, ?, ?)
                ''', [(
                    dropout.timestamp,
                    dropout.duration,
                    self._pk,
                ) for dropout in self._dropouts])

            # logged_messages
            cur.executemany('''
                INSERT INTO ULogMessageLogging
                (LogLevel, Timestamp, Message, ULogId)
                VALUES
                (?, ?, ?, ?)
                ''', [(
                    message.log_level,
                    message.timestamp,
                    message.message,
                    self._pk,
                ) for message in self._logged_messages])

            # logged_messages_tagged
            for tag, messages in self._logged_messages_tagged.items():
                cur.executemany('''
                    INSERT INTO ULogMessageLoggingTagged
                    (LogLevel, Timestamp, Tag, Message, ULogId)
                    VALUES
                    (?, ?, ?, ?, ?)
                    ''', [(
                        message.log_level,
                        message.timestamp,
                        tag,
                        message.message,
                        self._pk,
                    ) for message in messages])

            # message_formats
            for name, message_format in self._message_formats.items():
                cur.execute('''
                    INSERT INTO ULogMessageFormat
                    (Name, ULogId)
                    VALUES
                    (?, ?)
                    ''', (name, self._pk))
                format_id = cur.lastrowid
                cur.executemany('''
                    INSERT INTO ULogMessageFormatField
                    (FieldType, ArraySize, Name, MessageId)
                    VALUES
                    (?, ?, ?, ?)
                    ''', [(*field, format_id) for field in message_format.fields])

            # msg_info_dict
            cur.executemany('''
                INSERT INTO ULogMessageInfo
                (Key, Value, Typename, ULogId)
                VALUES
                (?, ?, ?, ?)
                ''', [(
                    key,
                    value,
                    self._msg_info_dict_types[key],
                    self._pk,
                ) for key, value in self.msg_info_dict.items()])

            # msg_info_multiple_dict
            for key, lists in self.msg_info_multiple_dict.items():
                cur.execute('''
                    INSERT INTO ULogMessageInfoMultiple
                    (Key, Typename, ULogId)
                    VALUES
                    (?, ?, ?)
                    ''', (key, self._msg_info_multiple_dict_types[key], self._pk))
                message_id = cur.lastrowid
                for list_index, message_list in enumerate(lists):
                    cur.execute('''
                        INSERT INTO ULogMessageInfoMultipleList
                        (SeriesIndex, MessageId)
                        VALUES
                        (?, ?)
                        ''', (list_index, message_id))
                    list_id = cur.lastrowid
                    cur.executemany('''
                        INSERT INTO ULogMessageInfoMultipleListElement
                        (SeriesIndex, Value, ListId)
                        VALUES
                        (?, ?, ?)
                        ''', [(
                            series_index,
                            value,
                            list_id,
                        ) for series_index, value in enumerate(message_list)])

            # initial_parameters
            cur.executemany('''
                INSERT INTO ULogInitialParameter
                (Key, Value, ULogId)
                VALUES
                (?, ?, ?)
                ''', [(
                    key,
                    value,
                    self._pk,
                ) for key, value in self.initial_parameters.items()])

            # _default_parameters
            for default_type, parameters in self._default_parameters.items():
                cur.executemany('''
                    INSERT INTO ULogDefaultParameter
                    (DefaultType, Key, Value, ULogId)
                    VALUES
                    (?, ?, ?, ?)
                    ''', [(
                        default_type,
                        key,
                        value,
                        self._pk,
                    ) for key, value in parameters.items()])

            # changed_parameters
            cur.executemany('''
                INSERT INTO ULogChangedParameter
                (Timestamp, Key, Value, ULogId)
                VALUES
                (?, ?, ?, ?)
                ''', [(
                    timestamp,
                    key,
                    value,
                    self._pk,
                ) for timestamp, key, value in self.changed_parameters])

            cur.close()

    def delete(self):
        '''
        Deletes the ULog row and cascading rows from the database.
        '''
        if self._pk is None:
            raise KeyError('Cannot delete logs that are not in the database')

        with self._db() as con:
            cur = con.cursor()
            cur.execute('''
                DELETE FROM ULog
                WHERE Id = ?
            ''', (self._pk,)
            )
            cur.close()
        self._pk = None

    class DatabaseData(ULog.Data):
        '''
        Overrides the ULog.Data class since its constructor only
        reads ULog.MessageLogAdded objects, and we want to specify
        the fields directly.
        '''
        # pylint: disable=super-init-not-called,too-many-arguments
        def __init__(self, name, multi_id, msg_id, timestamp_idx, field_data, data=None):
            self.name = name
            self.multi_id = multi_id
            self.msg_id = msg_id
            self.timestamp_idx = timestamp_idx
            self.field_data = field_data
            self.data = data

    class DatabaseMessageDropout(ULog.MessageDropout):
        '''
        Overrides the ULog.MessageDropout class since its
        constructor is not suitable for out purpose.
        '''
        # pylint: disable=super-init-not-called
        def __init__(self, timestamp, duration):
            self.timestamp = timestamp
            self.duration = duration

    class DatabaseMessageFormat(ULog.MessageFormat):
        '''
        Overrides the ULog.MessageFormat class since its
        constructor is not suitable for out purpose.
        '''
        # pylint: disable=super-init-not-called
        def __init__(self, name, fields):
            self.name = name
            self.fields = fields

    class DatabaseMessageLogging(ULog.MessageLogging):
        '''
        Overrides the ULog.MessageLogging class since its
        constructor is not suitable for out purpose.
        '''
        # pylint: disable=super-init-not-called
        def __init__(self, log_level, timestamp, message):
            self.log_level = log_level
            self.timestamp = timestamp
            self.message = message

    class DatabaseMessageLoggingTagged(ULog.MessageLoggingTagged):
        '''
        Overrides the ULog.MessageLoggingTagged class since its
        constructor is not suitable for our purpose.
        '''
        # pylint: disable=super-init-not-called
        def __init__(self, log_level, tag, timestamp, message):
            self.log_level = log_level
            self.tag = tag
            self.timestamp = timestamp
            self.message = message
