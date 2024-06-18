"""Test"""
# pylint: disable=consider-using-from-import
# pylint: disable=c-extension-no-member

import PyQt5.QtWidgets as QtWidgets

app = QtWidgets.QApplication([])
window = QtWidgets.QWidget()
window.setWindowTitle('17 Lands Ratings')
window.setWindowOpacity(0.8)
window.show()
app.exec_()
