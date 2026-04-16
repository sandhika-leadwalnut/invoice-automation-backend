import React, { useState, useEffect } from 'react';
import axios from 'axios';
import {
    BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, Legend, ResponsiveContainer,
    PieChart, Pie, Cell, LineChart, Line
} from 'recharts';

export default function Metrics() {
    const [metrics, setMetrics] = useState(null);
    const [loading, setLoading] = useState(true);

    const [filters, setFilters] = useState({
        start_date: '',
        end_date: '',
        vendor_name: ''
    });

    const fetchMetrics = async () => {
        setLoading(true);
        try {
            const params = new URLSearchParams();
            if (filters.start_date) params.append('start_date', new Date(filters.start_date).toISOString());
            if (filters.end_date) {
                const endData = new Date(filters.end_date);
                endData.setHours(23, 59, 59, 999);
                params.append('end_date', endData.toISOString());
            }
            if (filters.vendor_name) params.append('vendor_name', filters.vendor_name);

            const response = await axios.get(`http://127.0.0.1:8000/verification/metrics?${params.toString()}`);
            setMetrics(response.data);
        } catch (error) {
            console.error('Error fetching metrics', error);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchMetrics();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [filters]);

    const handleFilterChange = (e) => {
        setFilters({ ...filters, [e.target.name]: e.target.value });
    };

    if (loading && !metrics) {
        return (
            <div className="flex justify-center items-center h-64">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600"></div>
            </div>
        );
    }

    const COLORS = {
        pending: '#f59e0b',
        accepted: '#10b981',
        edited: '#3b82f6',
        rejected: '#ef4444'
    };

    const statusData = metrics ? Object.entries(metrics.status_distribution).map(([name, value]) => ({
        name, value
    })) : [];

    return (
        <div className="space-y-6">
            {/* Filters Header */}
            <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200">
                <h2 className="text-xl font-semibold text-slate-800 mb-4">Metrics Dashboard</h2>
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <div>
                        <label className="block text-sm font-medium text-slate-700 mb-1">Start Date</label>
                        <input
                            type="date"
                            name="start_date"
                            value={filters.start_date}
                            onChange={handleFilterChange}
                            className="w-full border border-slate-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-slate-700 mb-1">End Date</label>
                        <input
                            type="date"
                            name="end_date"
                            value={filters.end_date}
                            onChange={handleFilterChange}
                            className="w-full border border-slate-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-slate-700 mb-1">Vendor Name</label>
                        <input
                            type="text"
                            name="vendor_name"
                            placeholder="Filter by vendor..."
                            value={filters.vendor_name}
                            onChange={handleFilterChange}
                            className="w-full border border-slate-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
                        />
                    </div>
                    <div className="flex items-end">
                        <button
                            onClick={() => setFilters({ start_date: '', end_date: '', vendor_name: '' })}
                            className="w-full bg-slate-100 text-slate-700 py-2 px-4 rounded-md hover:bg-slate-200 transition-colors"
                        >
                            Clear Filters
                        </button>
                    </div>
                </div>
            </div>

            {metrics && (
                <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                    {/* Summary Cards */}
                    <div className="flex flex-col gap-6 md:col-span-1">
                        <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200 flex flex-col items-center justify-center flex-1">
                            <h3 className="text-lg font-medium text-slate-500 mb-2 text-center">Received by Mail</h3>
                            <p className="text-5xl font-bold text-emerald-600">{metrics.total_email_invoices || 0}</p>
                        </div>
                        <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200 flex flex-col items-center justify-center flex-1">
                            <h3 className="text-lg font-medium text-slate-500 mb-2 text-center">Total Processed</h3>
                            <p className="text-5xl font-bold text-indigo-600">{metrics.total}</p>
                        </div>
                        <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200 flex flex-col items-center justify-center flex-1">
                            <h3 className="text-lg font-medium text-slate-500 mb-2 text-center">Pushed to Zoho</h3>
                            <p className="text-5xl font-bold text-sky-500">{metrics.total_zoho_pushed || 0}</p>
                        </div>
                    </div>

                    {/* Status Pie Chart */}
                    <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200 md:col-span-3">
                        <h3 className="text-lg font-medium text-slate-800 mb-4">Invoice Processing Status</h3>
                        <div className="h-64">
                            <ResponsiveContainer width="100%" height="100%">
                                <PieChart>
                                    <Pie
                                        data={statusData}
                                        cx="50%"
                                        cy="50%"
                                        innerRadius={60}
                                        outerRadius={90}
                                        paddingAngle={5}
                                        dataKey="value"
                                    >
                                        {statusData.map((entry, index) => (
                                            <Cell key={`cell-${index}`} fill={COLORS[entry.name] || '#8884d8'} />
                                        ))}
                                    </Pie>
                                    <RechartsTooltip />
                                    <Legend />
                                </PieChart>
                            </ResponsiveContainer>
                        </div>
                    </div>

                    {/* Vendor Bar Chart */}
                    <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200 md:col-span-4">
                        <h3 className="text-lg font-medium text-slate-800 mb-4">Top Vendors by Volume</h3>
                        <div className="h-80">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart
                                    data={metrics.vendors.slice(0, 10)}
                                    margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
                                >
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                                    <XAxis dataKey="vendor" axisLine={false} tickLine={false} />
                                    <YAxis axisLine={false} tickLine={false} />
                                    <RechartsTooltip cursor={{ fill: 'transparent' }} />
                                    <Bar dataKey="count" fill="#6366f1" radius={[4, 4, 0, 0]} />
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    </div>

                    {/* Timeline Line Chart */}
                    <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200 md:col-span-4">
                        <h3 className="text-lg font-medium text-slate-800 mb-4">Processed Invoices over Time</h3>
                        <div className="h-80">
                            <ResponsiveContainer width="100%" height="100%">
                                <LineChart
                                    data={metrics.timeline}
                                    margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
                                >
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                                    <XAxis dataKey="date" axisLine={false} tickLine={false} />
                                    <YAxis axisLine={false} tickLine={false} />
                                    <RechartsTooltip />
                                    <Line type="monotone" dataKey="count" stroke="#10b981" strokeWidth={3} activeDot={{ r: 8 }} />
                                </LineChart>
                            </ResponsiveContainer>
                        </div>
                    </div>

                </div>
            )}
        </div>
    );
}
