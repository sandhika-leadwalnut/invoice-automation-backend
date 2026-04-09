import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Link } from 'react-router-dom';
import { FileStack } from 'lucide-react';

export default function Dashboard() {
    const [invoices, setInvoices] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        fetchInvoices();
    }, []);

    const fetchInvoices = async () => {
        try {
            const response = await axios.get('http://127.0.0.1:8000/verification/invoices/pending');
            setInvoices(response.data);
        } catch (error) {
            console.error('Error fetching invoices:', error);
        } finally {
            setLoading(false);
        }
    };

    if (loading) {
        return (
            <div className="flex justify-center items-center h-64">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600"></div>
            </div>
        );
    }

    return (
        <div className="bg-white shadow overflow-hidden sm:rounded-md">
            <div className="px-4 py-5 border-b border-slate-200 sm:px-6">
                <h3 className="text-lg leading-6 font-medium text-slate-900">Pending Invoices</h3>
                <p className="mt-1 max-w-2xl text-sm text-slate-500">
                    Review these invoices before they are pushed to Zoho Books.
                </p>
            </div>
            <ul className="divide-y divide-slate-200">
                {invoices.length === 0 ? (
                    <li className="px-4 py-12 text-center text-slate-500">No pending invoices</li>
                ) : (
                    invoices.map((invoice) => (
                        <li key={invoice._id}>
                            <Link to={`/review/${invoice._id}`} className="block hover:bg-slate-50 transition">
                                <div className="px-4 py-4 sm:px-6 flex items-center justify-between">
                                    <div className="flex items-center">
                                        <FileStack className="h-6 w-6 text-indigo-400 mr-3" />
                                        <p className="text-sm font-medium text-indigo-600 truncate">
                                            Invoice ID: {invoice._id}
                                        </p>
                                        {invoice.vendor_exists === false && (
                                            <span className="ml-3 inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">
                                                Vendor doesn't exist
                                            </span>
                                        )}
                                    </div>
                                    <div className="ml-2 flex-shrink-0 flex">
                                        <p className="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-yellow-100 text-yellow-800">
                                            {invoice.status}
                                        </p>
                                    </div>
                                </div>
                                <div className="px-4 pb-4 sm:px-6">
                                    <p className="text-sm text-slate-500">
                                        Received at: {new Date(invoice.created_at).toLocaleString()}
                                    </p>
                                </div>
                            </Link>
                        </li>
                    ))
                )}
            </ul>
        </div>
    );
}
