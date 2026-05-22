"""UI subpackage for the AnkiVN Smart Image Picker add-on.

Modules in this subpackage own all Qt widgets and run exclusively on the
Qt main thread (Anki's UI thread). Worker-thread modules in the parent
package communicate with these modules through the cross-thread signal
hub defined in :mod:`ankivn_image_picker.ui.worker_bus`.
"""
