import tkinter as tk
from app import DiskAnalyzerApp

def main():
    root = tk.Tk()
    app = DiskAnalyzerApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()