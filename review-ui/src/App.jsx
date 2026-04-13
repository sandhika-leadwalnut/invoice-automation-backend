import React from 'react';
import { BrowserRouter, Routes, Route, Link } from 'react-router-dom';
import Dashboard from './Dashboard';
import Review from './Review';
import Metrics from './Metrics';

function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen">
        <header className="bg-white shadow">
          <div className="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
            <h1 className="text-3xl font-bold text-slate-900 tracking-tight">Invoice Verification Panel</h1>
            <nav className="flex space-x-4">
              <Link to="/" className="text-slate-600 hover:text-indigo-600 font-medium">Dashboard</Link>
              <Link to="/metrics" className="text-slate-600 hover:text-indigo-600 font-medium">Metrics</Link>
            </nav>
          </div>
        </header>
        <main>
          <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/metrics" element={<Metrics />} />
              <Route path="/review/:id" element={<Review />} />
            </Routes>
          </div>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;
