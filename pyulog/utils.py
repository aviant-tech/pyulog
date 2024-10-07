#! /usr/bin/env python
"""
Check if a log file was armed
"""

import mmap
import struct
import io
import argparse
from typing import Optional, Tuple
from .core import ULog

ULOG_MESSAGE_HEADER_SIZE = 3
MAX_SEARCH_LENGTH = 200000


def find_field_offset(message_format: ULog.MessageFormat, target_field: str) -> int:
    """Find the offset of a field in a message format"""
    offset = 0
    for data_type, array_length, field_name in message_format.fields:
        if field_name == "":
            continue
        if field_name == target_field:
            return offset

        array_length = int(array_length) if array_length else 1

        try:
            offset += ULog.get_field_size(data_type) * array_length
        except ValueError as exc:
            raise ValueError(f"Unknown data type: {data_type} in field {target_field}") from exc

    raise ValueError(f"Field {target_field} not found in format string.")


def get_subscription_id(m_file: mmap.mmap, msg_name: str, max_search_len: int) -> Tuple[bytes, int]:
    """
    Find subscription ID for the given message name
    :param f: mmap object
    :param msg_name: message name
    :param max_search_len: Optional maximum length to search in the file
    """

    msg_name_b = msg_name.encode()

    m_file.seek(ULog.HEADER_SIZE)

    while m_file.tell() < max_search_len:
        header_and_id = m_file.read(ULOG_MESSAGE_HEADER_SIZE)
        if len(header_and_id) < ULOG_MESSAGE_HEADER_SIZE:
            # EOF
            raise ValueError(f"Could not find subscription for {msg_name}")
        msg_size, msg_type = struct.unpack("<HB", header_and_id)

        if msg_type != ord('A'):
            m_file.seek(msg_size, io.SEEK_CUR)  # Skip the rest of the message
            continue

        msg_bytes = m_file.read(msg_size)
        if len(msg_bytes) < msg_size or len(msg_bytes) < 3:
            # EOF before reading the full message
            raise ValueError(f"Could not find subscription for {msg_name}")
        _, msg_id = struct.unpack("<BH", msg_bytes[:3])
        if msg_bytes[3:] != msg_name_b:
            # Some other message type, continue searching
            continue

        return msg_id

    raise ValueError(f"Could not find subscription for {msg_name}")


def reverse_search_for_arm(
        m_file: mmap.mmap,
        vehicle_status_id: int,
        arm_reason_offset: int) -> bool:
    """Check if the vehicle has been armed based on the latest arming reason"""

    offset = len(m_file)
    # Keep track of how far towards the beginning of the log we have searched
    searched_to = len(m_file)
    armed = None
    while offset >= ULog.HEADER_SIZE:
        offset = m_file.rfind(ULog.SYNC_BYTES, 0, offset)
        if offset == -1:
            # Perhaps no more sync points, start from the beginning of the log
            offset = ULog.HEADER_SIZE
            m_file.seek(offset)
        else:
            m_file.seek(offset + len(ULog.SYNC_BYTES))

        while m_file.tell() < searched_to:
            header_and_id = m_file.read(ULOG_MESSAGE_HEADER_SIZE + 2)
            if len(header_and_id) < ULOG_MESSAGE_HEADER_SIZE + 2:
                break  # EOF
            msg_size, msg_type, msg_id = struct.unpack("<HBH", header_and_id)
            if msg_type != ord('D') or msg_id != vehicle_status_id:
                m_file.seek(msg_size - 2, io.SEEK_CUR)  # Skip the rest
                continue

            message_bytes = m_file.read(msg_size - 2)
            if len(message_bytes) < msg_size - 2:
                break  # EOF

            armed = message_bytes[arm_reason_offset] != 0
            if armed:
                # We know that the vehicle was armed, return early
                return True
            # Might have armed after this message, search rest of log
            armed = False

        searched_to = offset
        if armed is not None:
            # Searched to end, and found a valid arm status
            return armed

        offset -= 1

    if armed is None:
        raise ValueError("No vehicle_status messages found in log file.")

    return armed


def check_if_log_was_armed(
        ulog_path: str,
        max_search_len: Optional[int] = MAX_SEARCH_LENGTH) -> bool:
    """
    This method checks if the vehicle was armed at any point during the log
    file without parsing the entire file. This is done by searching for the
    latest arming reason in the vehicle_status messages.
    """
    message_name = "vehicle_status"
    field_name = "latest_arming_reason"

    ulog = ULog(ulog_path, parse_header_only=True)

    message_format = ulog.message_formats.get(message_name)
    if message_format is None:
        raise ValueError(f"Could not find format definition for {message_name}")

    with open(ulog_path, 'rb') as file:
        # Memory map the file for faster access
        with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as f_mem:
            if max_search_len is None:
                max_search_len = len(f_mem)
            else:
                max_search_len = min(len(f_mem), max_search_len)

            vehicle_status_id = get_subscription_id(f_mem, message_name, max_search_len)
            arm_reason_offset = find_field_offset(message_format, field_name)

            return reverse_search_for_arm(f_mem, vehicle_status_id, arm_reason_offset)


def main():
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Check if vehicle was armed during the log file')
    parser.add_argument('filename', metavar='file.ulg', help='ULog input file')
    args = parser.parse_args()
    ulog_file_name = args.filename
    log_was_armed = check_if_log_was_armed(ulog_file_name)
    print(f"Log was armed: {log_was_armed}")
