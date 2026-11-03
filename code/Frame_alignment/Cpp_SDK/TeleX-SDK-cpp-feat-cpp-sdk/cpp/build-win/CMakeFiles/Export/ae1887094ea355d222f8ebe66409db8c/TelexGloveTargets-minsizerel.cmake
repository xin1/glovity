#----------------------------------------------------------------
# Generated CMake target import file for configuration "MinSizeRel".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "TelexGlove::telex_glove" for configuration "MinSizeRel"
set_property(TARGET TelexGlove::telex_glove APPEND PROPERTY IMPORTED_CONFIGURATIONS MINSIZEREL)
set_target_properties(TelexGlove::telex_glove PROPERTIES
  IMPORTED_IMPLIB_MINSIZEREL "${_IMPORT_PREFIX}/lib/telex_glove.lib"
  IMPORTED_LOCATION_MINSIZEREL "${_IMPORT_PREFIX}/bin/telex_glove.dll"
  )

list(APPEND _cmake_import_check_targets TelexGlove::telex_glove )
list(APPEND _cmake_import_check_files_for_TelexGlove::telex_glove "${_IMPORT_PREFIX}/lib/telex_glove.lib" "${_IMPORT_PREFIX}/bin/telex_glove.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
