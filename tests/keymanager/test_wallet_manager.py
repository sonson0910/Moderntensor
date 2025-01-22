# tests/keymanager/test_wallet_manager.py

import os
import json
import pytest
import logging
from unittest.mock import patch

from sdk.config.settings import settings, logger  # Global logger & settings
from sdk.keymanager.encryption_utils import (
    get_or_create_salt,
    generate_encryption_key,
    get_cipher_suite,
)
from sdk.keymanager.coldkey_manager import ColdKeyManager
from sdk.keymanager.hotkey_manager import HotKeyManager
from sdk.keymanager.wallet_manager import WalletManager

# -------------------------------------------------------------------
# FIXTURES
# -------------------------------------------------------------------

@pytest.fixture
def temp_coldkey_dir(tmp_path):
    """
    Creates a temporary "coldkeys" folder inside the pytest-provided tmp_path.
    This avoids conflicts with existing directories or test data.
    """
    return tmp_path / "coldkeys"

@pytest.fixture
def coldkey_manager(temp_coldkey_dir):
    """
    Initializes a ColdKeyManager for testing, using the temporary directory 
    created by the temp_coldkey_dir fixture.
    """
    return ColdKeyManager(base_dir=str(temp_coldkey_dir))

@pytest.fixture
def hotkey_manager(coldkey_manager):
    """
    Initializes a HotKeyManager that shares the same dictionary of coldkeys 
    from the ColdKeyManager fixture. This ensures that any coldkeys created 
    by coldkey_manager are immediately visible to hotkey_manager.
    """
    return HotKeyManager(
        coldkeys_dict=coldkey_manager.coldkeys,
        base_dir=coldkey_manager.base_dir,
        network=None,  # Can also specify Network.TESTNET
    )

@pytest.fixture
def wallet_manager(tmp_path):
    """
    Creates a WalletManager set to TESTNET network using a temporary base directory.
    This fixture tests all wallet operations in an isolated temp environment.
    """
    return WalletManager(network=settings.CARDANO_NETWORK, base_dir=str(tmp_path))

# -------------------------------------------------------------------
# TEST encryption_utils
# -------------------------------------------------------------------

def test_get_or_create_salt(temp_coldkey_dir):
    """
    Verify that get_or_create_salt correctly creates a new salt.bin file 
    when it does not exist, and reuses the same salt afterwards.
    """
    # Ensure the directory exists
    temp_coldkey_dir.mkdir(parents=True, exist_ok=True)
    salt_file = temp_coldkey_dir / "salt.bin"

    # First call => generates a new salt
    salt1 = get_or_create_salt(str(temp_coldkey_dir))
    assert salt_file.exists(), "salt.bin should be created on the first call"
    assert len(salt1) == 16, "Default salt length is 16 bytes"

    # Second call => should read the existing salt
    salt2 = get_or_create_salt(str(temp_coldkey_dir))
    assert salt1 == salt2, "The same salt should be returned on subsequent calls"

def test_generate_encryption_key():
    """
    Check that generate_encryption_key produces a base64 urlsafe 32-byte key 
    (which is typically 44 characters when encoded).
    """
    salt = b"1234567890abcdef"  # Exactly 16 bytes
    password = "mysecret"
    key = generate_encryption_key(password, salt)
    # Base64-url-encoded keys are typically 44 chars in length.
    assert len(key) == 44, "Base64-encoded 32-byte key should be ~44 characters"

def test_get_cipher_suite(temp_coldkey_dir):
    """
    Verify encryption and decryption using the Fernet cipher generated by get_cipher_suite.
    """
    cipher = get_cipher_suite("mypwd", str(temp_coldkey_dir))
    text = b"hello"
    enc = cipher.encrypt(text)
    dec = cipher.decrypt(enc)
    assert dec == text, "Decrypted text should match the original"

# -------------------------------------------------------------------
# TEST coldkey_manager
# -------------------------------------------------------------------

def test_create_coldkey(coldkey_manager):
    """
    Validate that create_coldkey:
      - Generates a 'mnemonic.enc' file 
      - Generates a 'hotkeys.json' file 
      - Updates the internal coldkeys dictionary in memory.
    """
    name = "testcold"
    password = "secret"

    coldkey_manager.create_coldkey(name, password)
    cdir = os.path.join(coldkey_manager.base_dir, name)

    # Check for expected files
    assert os.path.exists(os.path.join(cdir, "mnemonic.enc")), "mnemonic.enc must be created"
    assert os.path.exists(os.path.join(cdir, "hotkeys.json")), "hotkeys.json must be created"

    # Check in-memory dictionary
    assert name in coldkey_manager.coldkeys, "Coldkey should be stored in the manager's dictionary"
    assert "wallet" in coldkey_manager.coldkeys[name], "wallet key should exist in the coldkey entry"

def test_create_coldkey_duplicate(coldkey_manager):
    """
    Ensure that creating a coldkey with an existing name raises an exception 
    (assuming the code disallows duplicates).
    """
    name = "dupCk"
    coldkey_manager.create_coldkey(name, "pwd")

    # Attempt to create the same coldkey name again
    with pytest.raises(Exception) as excinfo:
        coldkey_manager.create_coldkey(name, "pwd2")
    # Check the exception message for duplicates
    assert "already exists" in str(excinfo.value).lower() or "duplicate" in str(excinfo.value).lower()

def test_load_coldkey(coldkey_manager):
    """
    Create a coldkey, remove it from in-memory dict, then call load_coldkey 
    to verify that it is properly loaded back.
    """
    name = "testcold2"
    password = "secret"
    coldkey_manager.create_coldkey(name, password)

    # Remove the coldkey from memory
    coldkey_manager.coldkeys.pop(name, None)

    # Reload coldkey from disk
    coldkey_manager.load_coldkey(name, password)
    assert name in coldkey_manager.coldkeys, "Coldkey should be loaded again"
    assert "wallet" in coldkey_manager.coldkeys[name], "Wallet must be present after loading"

def test_load_coldkey_file_notfound(coldkey_manager):
    """
    Loading a non-existing coldkey directory should raise FileNotFoundError.
    """
    with pytest.raises(FileNotFoundError):
        coldkey_manager.load_coldkey("non_existent", "pwd")

def test_load_coldkey_wrong_password(coldkey_manager):
    """
    Attempt to load a coldkey with an incorrect password. 
    Expect an exception if the implementation checks passwords.
    """
    name = "myck_wrongpwd"
    password = "okpwd"
    coldkey_manager.create_coldkey(name, password)

    with pytest.raises(Exception) as excinfo:
        coldkey_manager.load_coldkey(name, "wrongpwd")
    # Check that the error is related to an invalid password or decryption failure
    assert "invalid password" in str(excinfo.value).lower() or "decrypt" in str(excinfo.value).lower()

# -------------------------------------------------------------------
# TEST hotkey_manager
# -------------------------------------------------------------------

def test_generate_hotkey(coldkey_manager, hotkey_manager):
    """
    Confirm that generate_hotkey:
      - Creates an address and encrypted key data 
      - Updates the hotkeys.json file 
      - Returns the same encrypted data that is stored on disk.
    """
    name = "ck_hot"
    password = "pass"
    coldkey_manager.create_coldkey(name, password)

    hotkey_name = "myhot1"
    enc_data = hotkey_manager.generate_hotkey(name, hotkey_name)

    # Check in-memory structure
    ck_info = coldkey_manager.coldkeys[name]
    assert hotkey_name in ck_info["hotkeys"], "Hotkey must be stored in coldkey dict"

    # Check hotkeys.json file
    cdir = os.path.join(coldkey_manager.base_dir, name)
    with open(os.path.join(cdir, "hotkeys.json"), "r") as f:
        data = json.load(f)

    assert hotkey_name in data["hotkeys"], "hotkey_name should be in the file's data['hotkeys']"

    # Verify encrypted_data matches the function return
    assert enc_data == data["hotkeys"][hotkey_name]["encrypted_data"], \
        "Encrypted data should match between in-memory return and file storage"

def test_generate_hotkey_duplicate(coldkey_manager, hotkey_manager):
    """
    Generating a hotkey with a duplicate name should raise an exception 
    (assuming code disallows duplicates).
    """
    name = "ck_dup"
    coldkey_manager.create_coldkey(name, "pwd")

    hotkey_name = "hotA"
    hotkey_manager.generate_hotkey(name, hotkey_name)

    # Attempt to create the same hotkey name
    with pytest.raises(Exception) as excinfo:
        hotkey_manager.generate_hotkey(name, hotkey_name)
    assert "already exists" in str(excinfo.value).lower() or "duplicate" in str(excinfo.value).lower()

def test_import_hotkey_yes(monkeypatch, coldkey_manager, hotkey_manager):
    """
    Test importing an existing hotkey and mock user input to "yes", 
    indicating that the user allows overwriting.
    """
    name = "ck_hot_import"
    password = "pass"
    coldkey_manager.create_coldkey(name, password)

    hotkey_name = "importme"
    enc_data = hotkey_manager.generate_hotkey(name, hotkey_name)

    # Mock the user input to 'yes' for overwriting
    with patch("builtins.input", return_value="yes"):
        hotkey_manager.import_hotkey(name, enc_data, hotkey_name, overwrite=False)

def test_import_hotkey_no(monkeypatch, coldkey_manager, hotkey_manager, caplog):
    """
    Test importing an existing hotkey but the user chooses "no" to overwrite.
    Expect a warning log stating "User canceled overwrite => import aborted."
    """
    caplog.set_level(logging.WARNING)
    name = "ck_hot_import_no"
    password = "pass"
    coldkey_manager.create_coldkey(name, password)

    hotkey_name = "importno"
    enc_data = hotkey_manager.generate_hotkey(name, hotkey_name)

    # Mock the user input to 'no'
    with patch("builtins.input", return_value="no"):
        hotkey_manager.import_hotkey(name, enc_data, hotkey_name, overwrite=False)

    logs = caplog.text
    assert "User canceled overwrite => import aborted." in logs, \
        "Expected a warning message about canceled overwrite"

# -------------------------------------------------------------------
# TEST WalletManager END-TO-END
# -------------------------------------------------------------------

def test_wallet_manager_end_to_end(wallet_manager):
    """
    Simulate a full end-to-end scenario:
      1) Create a new coldkey
      2) Load that coldkey
      3) Generate a hotkey
      4) Import the same hotkey (choosing to overwrite)
      5) Confirm the hotkey remains stored after import
    """
    ck_name = "myck"
    password = "mypwd"
    wallet_manager.create_coldkey(ck_name, password)

    cdir = os.path.join(wallet_manager.base_dir, ck_name)
    assert os.path.exists(os.path.join(cdir, "mnemonic.enc")), "mnemonic.enc must be created"
    assert os.path.exists(os.path.join(cdir, "hotkeys.json")), "hotkeys.json must be created"

    # Load the coldkey
    wallet_manager.load_coldkey(ck_name, password)

    # Generate a hotkey
    hk_name = "hk1"
    encrypted_data = wallet_manager.generate_hotkey(ck_name, hk_name)
    with open(os.path.join(cdir, "hotkeys.json"), "r") as f:
        data = json.load(f)
    assert hk_name in data["hotkeys"], "Hotkey should exist in hotkeys.json"

    # Import hotkey => user says "y" => overwrite
    with patch("builtins.input", return_value="y"):
        wallet_manager.import_hotkey(ck_name, encrypted_data, hk_name, overwrite=False)

    with open(os.path.join(cdir, "hotkeys.json"), "r") as f:
        data2 = json.load(f)
    assert hk_name in data2["hotkeys"], "Hotkey should still be present after import"

def test_wallet_manager_import_hotkey_no(wallet_manager, caplog):
    """
    Check behavior when attempting to import a hotkey but the user declines 
    the overwrite (inputs "no").
    """
    ck_name = "ck2"
    password = "pass2"
    wallet_manager.create_coldkey(ck_name, password)
    wallet_manager.load_coldkey(ck_name, password)

    hk_name = "hotabc"
    encrypted_data = wallet_manager.generate_hotkey(ck_name, hk_name)

    # User chooses 'no' when asked about overwriting
    with patch("builtins.input", return_value="no"):
        wallet_manager.import_hotkey(ck_name, encrypted_data, hk_name, overwrite=False)

    logs = caplog.text
    assert "User canceled overwrite => import aborted." in logs, \
        "Expected a warning message about canceled overwrite"

def test_wallet_manager_wrong_password(wallet_manager):
    """
    Verify that load_coldkey raises an Exception if a wrong password is used, 
    assuming the coldkey_manager implementation checks passwords during decryption.
    """
    ck_name = "ck_wrongpwd"
    password = "secret"
    wallet_manager.create_coldkey(ck_name, password)

    # Try loading with a bad password
    with pytest.raises(Exception) as excinfo:
        wallet_manager.load_coldkey(ck_name, "wrongpwd")
    assert "invalid password" in str(excinfo.value).lower() or "failed to decrypt" in str(excinfo.value).lower()
